"""Item 22: when the spam scanner is allowed to run.

Household-wide (not per-sender) — email isn't attributable to one phone and
the scan acts on the shared inbox. Exactly one row persists; `get_schedule`
always returns a value (seeded with safe defaults the first time) and
`set_schedule` overwrites in place.

The scanner loop checks `within_window(now)` on every tick and skips the
scan unless BOTH:
  - `enabled` is True, and
  - `start_hour <= local_hour < end_hour` in the schedule's timezone.

Interval gating (don't scan more often than `interval_hours`) is enforced
by the loop against `last_scan_at`, which lives on this row too.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from app._store import Store
from app.settings import settings


@dataclass
class SpamScanSchedule:
    start_hour: int = 8          # inclusive
    end_hour: int = 20           # exclusive
    interval_hours: int = 2
    tz: str = "Asia/Jerusalem"
    enabled: bool = True
    last_scan_at: float = 0.0    # epoch seconds; 0.0 = never
    # Audit-only mode. Classification + logging runs normally; IMAP mutations
    # are skipped. Default True so the first week after shipping is always a
    # dry run; the user flips to False via `enable_spam_live_mode()` once the
    # audit log looks right. This is persisted (not an env setting) so the
    # live/dry switch doesn't require an add-on restart.
    dry_run: bool = True
    live_mode_at: float = 0.0    # epoch seconds the user flipped dry_run off


_store: Store[SpamScanSchedule] = Store(
    lambda: settings.spam_scan_schedule_path, SpamScanSchedule
)


def _load() -> SpamScanSchedule:
    """Returns the single schedule row, seeding defaults on first run."""
    rows = _store.load_list()
    if rows:
        return rows[0]
    row = SpamScanSchedule()
    _store.save_list([row])
    return row


def _save(row: SpamScanSchedule) -> None:
    _store.save_list([row])


def get_schedule() -> SpamScanSchedule:
    return _load()


def set_schedule(
    start_hour: int,
    end_hour: int,
    interval_hours: int,
    enabled: bool = True,
    tz: str | None = None,
) -> SpamScanSchedule:
    if not (0 <= start_hour <= 23):
        raise ValueError("start_hour must be 0-23")
    if not (0 <= end_hour <= 24):
        raise ValueError("end_hour must be 0-24")
    if end_hour <= start_hour:
        raise ValueError("end_hour must be strictly after start_hour")
    if interval_hours < 1:
        raise ValueError("interval_hours must be >= 1")
    current = _load()
    new = replace(
        current,
        start_hour=start_hour,
        end_hour=end_hour,
        interval_hours=interval_hours,
        enabled=enabled,
        tz=tz if tz is not None else current.tz,
    )
    _save(new)
    return new


def disable() -> SpamScanSchedule:
    row = replace(_load(), enabled=False)
    _save(row)
    return row


def enable() -> SpamScanSchedule:
    row = replace(_load(), enabled=True)
    _save(row)
    return row


def mark_scanned(at: float) -> None:
    _save(replace(_load(), last_scan_at=at))


def enable_live_mode() -> SpamScanSchedule:
    """Flip dry_run OFF — real IMAP moves start on the next scan. The caller
    (an agent tool) is expected to require explicit user intent; the handler
    stamps `live_mode_at` so the audit log shows exactly when the switch
    happened."""
    import time
    row = replace(_load(), dry_run=False, live_mode_at=time.time())
    _save(row)
    return row


def disable_live_mode() -> SpamScanSchedule:
    """Flip dry_run back ON. Instant kill switch if a bad classification goes
    out and the user wants the scanner to stop touching email."""
    row = replace(_load(), dry_run=True)
    _save(row)
    return row


def within_window(now: datetime, row: SpamScanSchedule | None = None) -> bool:
    """True if `now` falls inside the configured scan window (schedule's TZ)."""
    row = row if row is not None else _load()
    if not row.enabled:
        return False
    try:
        local = now.astimezone(ZoneInfo(row.tz))
    except Exception:
        # A bad tz saved by hand shouldn't silently freeze the scanner.
        local = now.astimezone(ZoneInfo("Asia/Jerusalem"))
    return row.start_hour <= local.hour < row.end_hour
