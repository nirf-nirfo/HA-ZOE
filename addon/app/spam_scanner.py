"""Item 22: spam scanner orchestrator.

One scan tick walks recent email, decides what each one is, and acts.
Decision tree in order:

  1. Allowlist (sender pattern in `settings.email_spam_allowlist`) → leave,
     log `verdict="allowlist"`, no LLM call.
  2. Sender-memory hit (prior learned verdict for exact sender) → act on
     the stored verdict, log `verdict="memory_hit_*"`, no LLM call. The
     biggest cost lever — a steady-state inbox routes most mail here.
  3. Otherwise, batch-classify through `spam_classifier.classify` (Haiku,
     prompt-cached, 10 emails/call).
     - verdict=spam  AND confidence >= 0.9 → move to Spam folder + learn
       sender + log.
     - everything else → leave in inbox + log the verdict. The user
       reviews the audit log via `show_spam_audit`, then either adds the
       sender to the allowlist, teaches the memory via
       `remember_sender_verdict`, or does nothing.

Mutations are double-gated:
  - `settings.email_write_enabled` (startup env var; hard safety switch)
  - `SpamScanSchedule.dry_run` (runtime toggle; defaults True for the
    first-week audit period). Flip to False via `enable_spam_live_mode`.

Either False → the audit log still records what WOULD have happened as
`dry_run_would_*`, but no IMAP writes occur.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app import spam_audit, spam_scan_schedule, spam_sender_memory
from app.email_backend import EmailMessage
from app.settings import settings

logger = logging.getLogger(__name__)

CONFIDENT_SPAM = 0.9


@dataclass
class ScanOutcome:
    scanned: int
    memory_hits: int
    allowlisted: int
    classified: int
    moved: int
    dry_run: bool


def _is_dry_run() -> bool:
    if not getattr(settings, "email_write_enabled", False):
        return True
    try:
        return spam_scan_schedule.get_schedule().dry_run
    except Exception:
        # If the schedule row can't load for any reason, fail safe: dry-run.
        logger.exception("Spam scanner: schedule load failed; forcing dry-run")
        return True


def _allowlist_patterns() -> list[str]:
    raw = getattr(settings, "email_spam_allowlist", "") or ""
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _matches_allowlist(from_addr: str, patterns: list[str]) -> bool:
    addr = (from_addr or "").lower()
    return any(p in addr for p in patterns if p)


async def _resolve_spam_folder(backend) -> str:
    """Explicit `email_spam_folder` setting wins; else auto-detect over IMAP."""
    explicit = (getattr(settings, "email_spam_folder", "") or "").strip()
    if explicit:
        return explicit
    try:
        return await backend.detect_spam_folder()
    except Exception:
        logger.exception("Spam scanner: folder detection failed")
        return ""


async def run_scan(backend, classify_fn=None) -> ScanOutcome:
    """One scan tick. Returns a ScanOutcome summary. Raises on fatal IMAP
    errors so the loop's heartbeat can mark the tick as failed."""
    from app import spam_classifier
    if classify_fn is None:
        classify_fn = spam_classifier.classify

    dry_run = _is_dry_run()
    outcome = ScanOutcome(
        scanned=0, memory_hits=0, allowlisted=0,
        classified=0, moved=0, dry_run=dry_run,
    )

    patterns = _allowlist_patterns()
    schedule = spam_scan_schedule.get_schedule()
    spam_folder = await _resolve_spam_folder(backend)

    # Scan a bounded window — 2× the configured interval so a dropped tick
    # doesn't miss anything on the next one.
    try:
        headers = await backend.list_recent(
            limit=50, since_hours=max(schedule.interval_hours * 2, 2),
        )
    except Exception:
        logger.exception("Spam scanner: list_recent failed")
        raise

    to_classify: list[EmailMessage] = []
    for header in headers:
        outcome.scanned += 1
        from_addr = header.from_addr or ""

        if _matches_allowlist(from_addr, patterns):
            outcome.allowlisted += 1
            spam_audit.append(
                uid=header.uid, from_addr=from_addr, subject=header.subject,
                verdict="allowlist", confidence=1.0,
                action="left_in_inbox", dry_run=dry_run,
                reason="sender in email_spam_allowlist",
            )
            continue

        memo = spam_sender_memory.get(from_addr)
        if memo is not None:
            outcome.memory_hits += 1
            spam_sender_memory.record_hit(from_addr)
            if memo.verdict == "spam":
                audit_action = "dry_run_would_move" if dry_run else "moved_to_spam"
                if not dry_run and spam_folder:
                    moved = await backend.move_to_folder(header.uid, spam_folder)
                    if moved:
                        outcome.moved += 1
                        audit_action = "moved_to_spam"
                    else:
                        audit_action = "move_failed"
                spam_audit.append(
                    uid=header.uid, from_addr=from_addr, subject=header.subject,
                    verdict="memory_hit_spam", confidence=1.0,
                    action=audit_action, dry_run=dry_run,
                    reason=f"sender-memory match ({memo.hits} prior)",
                )
            else:  # legit
                spam_audit.append(
                    uid=header.uid, from_addr=from_addr, subject=header.subject,
                    verdict="memory_hit_legit", confidence=1.0,
                    action="left_in_inbox", dry_run=dry_run,
                    reason=f"sender-memory match ({memo.hits} prior)",
                )
            continue

        try:
            full = await backend.fetch(header.uid)
        except Exception:
            logger.exception("Spam scanner: fetch(%s) failed", header.uid)
            continue
        if full is None:
            continue
        to_classify.append(full)

    if to_classify:
        outcome.classified = len(to_classify)
        classifications = classify_fn(to_classify)
        by_uid = {c.uid: c.verdict for c in classifications}
        for msg in to_classify:
            v = by_uid.get(msg.uid)
            if v is None:
                continue
            if v.verdict == "spam" and v.confidence >= CONFIDENT_SPAM:
                audit_action = "dry_run_would_move" if dry_run else "moved_to_spam"
                if not dry_run and spam_folder:
                    moved = await backend.move_to_folder(msg.uid, spam_folder)
                    if moved:
                        outcome.moved += 1
                        spam_sender_memory.set_verdict(msg.from_addr, "spam")
                        audit_action = "moved_to_spam"
                    else:
                        audit_action = "move_failed"
                spam_audit.append(
                    uid=msg.uid, from_addr=msg.from_addr, subject=msg.subject,
                    verdict="spam", confidence=v.confidence,
                    action=audit_action, dry_run=dry_run,
                    reason=v.reason,
                )
            else:
                spam_audit.append(
                    uid=msg.uid, from_addr=msg.from_addr, subject=msg.subject,
                    verdict=v.verdict, confidence=v.confidence,
                    action="left_in_inbox", dry_run=dry_run,
                    reason=v.reason,
                )

    spam_scan_schedule.mark_scanned(time.time())
    return outcome
