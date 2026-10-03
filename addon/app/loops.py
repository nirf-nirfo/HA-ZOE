"""Item 06 split: the six background loops.

Kept 1:1 with pre-split behavior; each loop imports its dependencies
directly. `spawn_loops()` is called from main.py's startup handler.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import (
    agenda, anchors, briefing, check_ins, email_watches, inbound_tracker,
    monitors, recurring_expenses, scheduled_actions, senders,
)
from app.agent_loop import _allowed_senders, _run_check_in
from app.briefing_compile import _compile_evening_briefing, _compile_morning_briefing
from app.claude_agent import _email_backend, get_known_entities
from app.confirmation import make_pending, store_pending
from app.ha_client import ha_client
from app.handlers.scheduled_actions import _auto_turn_off_later, _execute_control_action
from app.logging_config import logger
from app.reminders import pop_due
from app.status import _beat
from app.whatsapp import send_message

# Item 21 depends on Item 20's email_processor.process_email. If Item 20 hasn't
# merged yet (we may land first on main), skip gracefully instead of killing
# the whole app's import graph — the loop logs and no-ops until the module
# shows up. Once Item 20 lands this import succeeds and the loop processes
# matches normally.
try:
    from app import email_processor  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised in environments missing Item 20
    email_processor = None  # type: ignore[assignment]

_IL_TZ = ZoneInfo("Asia/Jerusalem")


# Meta's WhatsApp free-tier 24h window bookkeeping. We warn the user at ~23h so
# they have time to send a quick inbound and reopen the window before outbound
# messages start silently failing. Senders who haven't messaged in >48h are
# considered inactive-by-design and are not pinged.
_META_WINDOW_HOURS = 24
_WARN_AT_HOURS = 23
_INACTIVE_CUTOFF_HOURS = 48
_META_WINDOW_WARNING = (
    "⏰ עוד שעה החלון של Meta נסגר. שלח לי הודעה קצרה (\"היי\") כדי לפתוח אותו מחדש."
)


async def _daily_briefing_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("daily_briefing")
        try:
            now_il = datetime.now(_IL_TZ)
            today_str = now_il.strftime("%Y-%m-%d")
            tomorrow = now_il + timedelta(days=1)
            for cfg in briefing.all_enabled():
                try:
                    # Morning briefing
                    if cfg.enabled and cfg.last_sent_date != today_str:
                        scheduled_morning = now_il.replace(hour=cfg.hour, minute=cfg.minute, second=0, microsecond=0)
                        if now_il >= scheduled_morning:
                            text = await _compile_morning_briefing(cfg.sender, now_il)
                            await send_message(cfg.sender, text)
                            briefing.mark_sent(cfg.sender, today_str)
                            logger.info("Sent morning briefing to %s for %s", cfg.sender, today_str)
                    # Evening briefing
                    if cfg.evening_enabled and cfg.last_evening_sent_date != today_str:
                        scheduled_eve = now_il.replace(
                            hour=cfg.evening_hour, minute=cfg.evening_minute, second=0, microsecond=0
                        )
                        if now_il >= scheduled_eve:
                            text = await _compile_evening_briefing(cfg.sender, now_il, tomorrow)
                            await send_message(cfg.sender, text)
                            briefing.mark_evening_sent(cfg.sender, today_str)
                            logger.info("Sent evening briefing to %s for %s", cfg.sender, today_str)
                except Exception:
                    logger.exception("Daily briefing loop: failed for %s", cfg.sender)
            # Insert any recurring household bills due today (idempotent).
            try:
                recurring_expenses.insert_due_today()
            except Exception:
                logger.exception("Daily briefing loop: recurring expenses tick failed")
            # Keep stores tidy; nothing before today is ever read again.
            agenda.purge_before(today_str)
            anchors.purge_suppressions_before(today_str)
        except Exception:
            _beat("daily_briefing", ok=False)
            logger.exception("Daily briefing loop: tick failed")


async def _scheduled_action_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("scheduled_action")
        try:
            due = scheduled_actions.pop_due()
        except Exception:
            _beat("scheduled_action", ok=False)
            logger.exception("Scheduled action loop: pop_due failed")
            continue
        for a in due:
            try:
                known_entities = get_known_entities()
                entity_def = known_entities.get(a.entity_id)
                if entity_def and entity_def.get("risky"):
                    # Same yes/confirm gate as an immediate risky action — never auto-run these.
                    pending = make_pending(a.entity_id, a.domain, a.service, a.service_data, a.description)
                    store_pending(a.sender, [pending])
                    logger.info("Scheduled risky action %s due — queued for confirmation", a.id)
                    await send_message(
                        a.sender, f"Time to run: {a.description}. This is sensitive — reply 'yes' to confirm."
                    )
                    continue
                logger.info("Running scheduled action %s: %s", a.id, a.description)
                reply = await _execute_control_action(a.entity_id, a.domain, a.service, a.service_data, a.description)
                if a.duration_minutes and a.service == "turn_on" and "✅" in reply:
                    asyncio.create_task(
                        _auto_turn_off_later(a.sender, a.entity_id, a.domain, a.entity_name, a.duration_minutes)
                    )
                    reply += f" (will auto turn-off in {a.duration_minutes:g} min)"
                await send_message(a.sender, f"⏰ Scheduled action: {reply}")
            except Exception:
                logger.exception("Scheduled action loop: failed to run %s", a.id)


async def _monitor_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("monitor")
        now = time.time()
        try:
            due = monitors.get_due(now)
        except Exception:
            _beat("monitor", ok=False)
            logger.exception("Monitor loop: get_due failed")
            continue
        for m in due:
            try:
                live = await ha_client.get_states([m.entity_id])
                actual = live.get(m.entity_id, {}).get("state", "unknown")
                # Edge-triggered: alert on the first check that's bad, and whenever the device
                # leaves the expected state — but not repeatedly while it stays bad.
                if actual != m.expected_state and (m.last_state is None or m.last_state == m.expected_state):
                    logger.info("Monitor %s: %s is %s (expected %s) — alerting", m.id, m.entity_id, actual, m.expected_state)
                    await send_message(m.sender, m.alert_text)
                monitors.advance(m.id, now + m.interval_minutes * 60, actual)
            except Exception:
                logger.exception("Monitor loop: check failed for %s", m.id)
        try:
            monitors.purge_expired(now)
        except Exception:
            logger.exception("Monitor loop: purge_expired failed")


async def _check_in_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("check_in")
        try:
            due = check_ins.pop_due()
        except Exception:
            _beat("check_in", ok=False)
            logger.exception("Check-in loop: pop_due failed")
            continue
        for c in due:
            try:
                logger.info("Firing check-in %s for %s", c.id, c.sender)
                await _run_check_in(
                    c.sender, c.id, c.prompt, c.last_fired_at, c.last_fired_text
                )
            except Exception:
                logger.exception("Check-in loop: failed to run %s", c.id)


async def _meta_window_loop() -> None:
    """Warns each allowed sender once, at ~23h into Meta's 24h free-tier window,
    so they can send a quick inbound and keep the window open. Senders inactive
    for more than 48h are skipped (they aren't actively conversing)."""
    warn_lo = _WARN_AT_HOURS * 3600
    warn_hi = _META_WINDOW_HOURS * 3600
    inactive_cutoff = _INACTIVE_CUTOFF_HOURS * 3600
    while True:
        await asyncio.sleep(300)
        _beat("meta_window")
        try:
            entries = inbound_tracker.all_senders()
        except Exception:
            _beat("meta_window", ok=False)
            logger.exception("Meta-window loop: all_senders failed")
            continue
        now = time.time()
        allowed = _allowed_senders()
        for sender, entry in entries.items():
            try:
                if sender not in allowed:
                    continue
                elapsed = now - entry.last_inbound_at
                if elapsed >= inactive_cutoff:
                    continue
                if entry.warned_23h:
                    continue
                if warn_lo <= elapsed < warn_hi:
                    logger.info("Meta-window loop: warning %s (%s) at %.1fh", sender, senders.resolve(sender), elapsed / 3600)
                    await send_message(sender, _META_WINDOW_WARNING)
                    inbound_tracker.mark_warned(sender)
            except Exception:
                logger.exception("Meta-window loop: failed for %s", sender)


def _watch_matches(watch: "email_watches.EmailWatch", msg) -> bool:
    """Returns True iff this snippet-level EmailMessage matches every non-empty
    filter on the watch (AND over configured fields; case-insensitive).

    Empty filter fields are skipped — a watch with only from_contains set
    behaves as "every email from that sender". Nothing is "matches everything":
    the tool handler already refuses a watch with all three filters blank.
    """
    needles = [
        (watch.from_contains, msg.from_addr),
        (watch.subject_contains, msg.subject),
        (watch.body_contains, msg.snippet),
    ]
    for needle, haystack in needles:
        if not needle:
            continue
        if needle.lower() not in (haystack or "").lower():
            return False
    return True


async def _email_watch_loop() -> None:
    """Item 21: evaluates each enabled email watch at its own interval, hands
    newly matched messages to `email_processor.process_email`, and remembers
    the processed UIDs so the same message is never acted on twice.

    Base cadence is 60s; each watch respects its own interval_minutes.
    """
    while True:
        await asyncio.sleep(60)
        _beat("email_watch")
        try:
            watches = email_watches.list_watches()
        except Exception:
            _beat("email_watch", ok=False)
            logger.exception("Email watch loop: list_watches failed")
            continue
        if not watches:
            # No-op tick: the heartbeat above is enough; nothing to do.
            continue
        if email_processor is None:
            # Item 20 hasn't landed yet; log once per tick at debug (not warn)
            # so a long gap between items doesn't spam the add-on log.
            logger.debug("Email watch loop: email_processor unavailable; skipping tick")
            continue
        now = datetime.now(timezone.utc)
        for watch in watches:
            try:
                if not watch.enabled:
                    continue
                if watch.last_checked_at is not None:
                    due_at = watch.last_checked_at + timedelta(minutes=watch.interval_minutes)
                    if now < due_at:
                        continue
                # Pull a generous window of recent headers so a transient IMAP
                # hiccup or a sender-side delay can't drop a message between ticks.
                since_hours = max((watch.interval_minutes // 60) * 2, 2)
                recent = await _email_backend.list_recent(
                    limit=20, since_hours=since_hours
                )
                already = set(watch.last_processed_uids)
                matched_uids: list[str] = []
                for header in recent:
                    if header.uid in already:
                        continue
                    if not _watch_matches(watch, header):
                        continue
                    try:
                        full = await _email_backend.fetch(header.uid)
                    except Exception:
                        logger.exception(
                            "Email watch loop: fetch(%s) failed for watch %s (%s)",
                            header.uid, watch.id, watch.name,
                        )
                        continue
                    if full is None:
                        continue
                    try:
                        result = email_processor.process_email(full)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception(
                            "Email watch loop: process_email failed for uid=%s watch=%s (%s)",
                            header.uid, watch.id, watch.name,
                        )
                        # Still mark the uid processed so a persistently bad
                        # message doesn't retry forever and starve newer ones.
                    matched_uids.append(header.uid)
                if matched_uids:
                    email_watches.mark_uids_processed(watch.id, matched_uids)
                email_watches.update_last_checked(watch.id, now)
            except Exception:
                logger.exception(
                    "Email watch loop: watch %s (%s) failed; continuing",
                    getattr(watch, "id", "?"), getattr(watch, "name", "?"),
                )


async def _reminder_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("reminder")
        # Guard the whole tick: a bad reminder or a failed send must never kill the
        # loop, or reminders would silently stop firing forever while the app stays up.
        try:
            due = pop_due()
        except Exception:
            _beat("reminder", ok=False)
            logger.exception("Reminder loop: pop_due failed")
            continue
        for reminder in due:
            try:
                logger.info("Firing reminder %s for %s", reminder.id, reminder.sender)
                await send_message(reminder.sender, f"⏰ {reminder.text}")
            except Exception:
                logger.exception("Reminder loop: failed to send reminder %s", reminder.id)


def spawn_loops() -> None:
    """Called from FastAPI startup. Fires all background loops as tasks."""
    asyncio.create_task(_reminder_loop())
    asyncio.create_task(_monitor_loop())
    asyncio.create_task(_scheduled_action_loop())
    asyncio.create_task(_daily_briefing_loop())
    asyncio.create_task(_check_in_loop())
    asyncio.create_task(_meta_window_loop())
    # Item 21: hourly inbox watches. Base cadence 60s with per-watch
    # interval_minutes gating; see `_email_watch_loop` for the full contract.
    asyncio.create_task(_email_watch_loop())
