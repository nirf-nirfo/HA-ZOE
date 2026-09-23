import calendar
import json
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.logging_config import logger
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


def _load() -> list[CheckIn]:
    path = Path(settings.check_ins_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [CheckIn(**c) for c in data]
    except Exception:
        logger.warning("Could not load check_ins file, starting fresh")
        return []


def _save(items: list[CheckIn]) -> None:
    path = Path(settings.check_ins_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(c) for c in items], ensure_ascii=False),
        encoding="utf-8",
    )


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


def add(sender: str, prompt: str, next_at: float, recurrence: str | None = None) -> CheckIn:
    items = _load()
    c = CheckIn(
        id=str(uuid.uuid4())[:6],
        sender=sender,
        prompt=prompt,
        next_at=next_at,
        recurrence=recurrence if recurrence in RECURRENCES else None,
    )
    items.append(c)
    _save(items)
    return c


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


def pop_due() -> list[CheckIn]:
    """Removes/reschedules check-ins whose time has come, returns them for firing."""
    now = time.time()
    items = _load()
    due = [c for c in items if c.next_at <= now]
    if not due:
        return []
    remaining = [c for c in items if c.next_at > now]
    for c in due:
        if c.recurrence in RECURRENCES:
            remaining.append(replace(c, next_at=_next_occurrence(c.next_at, c.recurrence, now)))
    _save(remaining)
    return due
