import calendar
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app._store import Store
from app.settings import settings

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# A "check-in" is a scheduled self-invocation: at a chosen time, ZOE wakes up,
# reads whatever state the check-in's prompt says to read (lists, agenda,
# expenses, device state), and sends a freshly composed WhatsApp message to
# the user. Distinct from a reminder (static text) and a scheduled action
# (device command). Recurrence follows the reminders convention.

RECURRENCES = {"daily", "weekly", "monthly", "yearly"}


@dataclass
class CheckIn:
    id: str
    sender: str
    prompt: str  # short instruction to ZOE-at-that-time (what to read, what to ask)
    next_at: float  # Unix ts
    recurrence: str | None = None
    # Overrides `recurrence` when set: fire every N minutes (used for "check every
    # hour on my progress" work-session polling; sub-daily cadences that daily/weekly
    # can't express).
    interval_minutes: int | None = None


_store: Store[CheckIn] = Store(lambda: settings.check_ins_path, CheckIn)
_from_dict = _store._from_dict


def _load() -> list[CheckIn]:
    return _store.load_list()


def _save(items: list[CheckIn]) -> None:
    _store.save_list(items)


def _add_period(dt: datetime, recurrence: str) -> datetime:
    if recurrence == "daily":
        return dt + timedelta(days=1)
    if recurrence == "weekly":
        return dt + timedelta(weeks=1)
    if recurrence == "monthly":
        month = dt.month % 12 + 1
        year = dt.year + (1 if dt.month == 12 else 0)
        day = min(dt.day, calendar.monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)
    if recurrence == "yearly":
        year = dt.year + 1
        day = min(dt.day, calendar.monthrange(year, dt.month)[1])
        return dt.replace(year=year, day=day)
    return dt


def _next_occurrence(next_at: float, recurrence: str, now: float) -> float:
    dt = datetime.fromtimestamp(next_at, _IL_TZ).replace(tzinfo=None)
    while True:
        dt = _add_period(dt, recurrence)
        ts = dt.replace(tzinfo=_IL_TZ).timestamp()
        if ts > now:
            return ts


def add(
    sender: str,
    prompt: str,
    next_at: float,
    recurrence: str | None = None,
    interval_minutes: int | None = None,
) -> CheckIn:
    items = _load()
    c = CheckIn(
        id=str(uuid.uuid4())[:6],
        sender=sender,
        prompt=prompt,
        next_at=next_at,
        # interval_minutes wins over recurrence if both are somehow set.
        recurrence=None if interval_minutes else (recurrence if recurrence in RECURRENCES else None),
        interval_minutes=int(interval_minutes) if interval_minutes else None,
    )
    items.append(c)
    _save(items)
    return c


def _next_from_interval(next_at: float, interval_minutes: int, now: float) -> float:
    """Steps `next_at` forward by `interval_minutes` until it lands strictly after
    `now`. Catches up gracefully if the loop missed ticks (e.g. Anthropic outage)."""
    ts = next_at + interval_minutes * 60
    while ts <= now:
        ts += interval_minutes * 60
    return ts


def list_for_sender(sender: str) -> list[CheckIn]:
    now = time.time()
    return sorted(
        (c for c in _load() if c.sender == sender and c.next_at > now),
        key=lambda c: c.next_at,
    )


def find_matching(sender: str, identifier: str) -> list[CheckIn]:
    ident = identifier.strip()
    pending = list_for_sender(sender)
    by_id = [c for c in pending if c.id == ident]
    if by_id:
        return by_id
    needle = ident.lower()
    return [c for c in pending if needle and needle in c.prompt.lower()]


def remove(check_in_id: str, sender: str) -> bool:
    items = _load()
    remaining = [c for c in items if not (c.id == check_in_id and c.sender == sender)]
    if len(remaining) == len(items):
        return False
    _save(remaining)
    return True


def count_all() -> int:
    """Total number of stored check-ins (all senders). Summary for /admin/status."""
    return len(_load())


def next_fire_at() -> float | None:
    """Earliest future next_at across all senders, or None if nothing pending."""
    now = time.time()
    times = [c.next_at for c in _load() if c.next_at > now]
    return min(times) if times else None


def pop_due() -> list[CheckIn]:
    """Removes/reschedules check-ins whose time has come, returns them for firing."""
    now = time.time()
    items = _load()
    due = [c for c in items if c.next_at <= now]
    if not due:
        return []
    remaining = [c for c in items if c.next_at > now]
    for c in due:
        if c.interval_minutes:
            remaining.append(replace(c, next_at=_next_from_interval(c.next_at, c.interval_minutes, now)))
        elif c.recurrence in RECURRENCES:
            remaining.append(replace(c, next_at=_next_occurrence(c.next_at, c.recurrence, now)))
    _save(remaining)
    return due
