"""Item 22: append-only audit log of every spam-scanner decision.

Every scan logs one row per classified email with: UID, sender, subject,
verdict, confidence, action, timestamp, and whether the action was taken
or suppressed by dry-run.

Rolling 30-day retention — older rows are purged on each append so the
file never grows unboundedly.

Used by:
  - the `show_spam_audit(days)` tool so the user can review what happened
    (and what WOULD have happened during the dry-run week), and
  - the admin status endpoint for a top-level count.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app._store import Store
from app.settings import settings

_RETENTION_SECONDS = 30 * 24 * 3600


@dataclass
class SpamAuditEntry:
    id: str
    at: float                        # epoch seconds
    uid: str                         # IMAP UID
    from_addr: str
    subject: str
    verdict: str                     # "spam" | "legit" | "uncertain" | "memory_hit_spam" | "memory_hit_legit" | "allowlist"
    confidence: float                # 0.0-1.0; set by classifier, else 1.0 for memory/allowlist
    action: str                      # "moved_to_spam" | "left_in_inbox" | "awaiting_confirmation" | "dry_run_would_move" | "dry_run_would_leave"
    dry_run: bool
    reason: str = ""                 # short human-visible reason the classifier gave, if any


_store: Store[SpamAuditEntry] = Store(lambda: settings.spam_audit_path, SpamAuditEntry)


def _load() -> list[SpamAuditEntry]:
    return _store.load_list()


def _save(entries: list[SpamAuditEntry]) -> None:
    _store.save_list(entries)


def _prune(entries: list[SpamAuditEntry]) -> list[SpamAuditEntry]:
    cutoff = time.time() - _RETENTION_SECONDS
    return [e for e in entries if e.at >= cutoff]


def append(
    *,
    uid: str,
    from_addr: str,
    subject: str,
    verdict: str,
    confidence: float,
    action: str,
    dry_run: bool,
    reason: str = "",
) -> SpamAuditEntry:
    entries = _prune(_load())
    entry = SpamAuditEntry(
        id=uuid.uuid4().hex[:12],
        at=time.time(),
        uid=uid,
        from_addr=from_addr,
        subject=subject[:200],  # bound in case of absurd subjects
        verdict=verdict,
        confidence=confidence,
        action=action,
        dry_run=dry_run,
        reason=reason[:300],
    )
    entries.append(entry)
    _save(entries)
    return entry


def recent(days: int = 7, limit: int = 100) -> list[SpamAuditEntry]:
    cutoff = time.time() - max(days, 0) * 24 * 3600
    rows = [e for e in _load() if e.at >= cutoff]
    rows.sort(key=lambda e: e.at, reverse=True)
    return rows[: max(limit, 0)]


def count() -> int:
    return len(_load())
