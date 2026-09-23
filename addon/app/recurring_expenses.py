import calendar
import json
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.logging_config import logger
from app.settings import settings
from app import expenses as expenses_mod

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# Household recurring bills (rent, utilities, subscriptions). Each day the
# housekeeping tick runs `insert_due_today` — for every recurring whose
# day_of_month matches today (and whose month_pattern allows this month), one
# expense row is inserted with source='recurring'. last_inserted_date dedupes
# across ticks and restarts, and a short-month day is clamped to month-end.

_MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


@dataclass
class RecurringExpense:
    id: str
    name: str
    amount: float
    day_of_month: int
    month_pattern: str  # "monthly" or comma-separated month names ("jan,jul")
    category: str
    payment_method: str
    last_inserted_date: str | None
    created_at: float


def _load() -> list[RecurringExpense]:
    path = Path(settings.recurring_expenses_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [RecurringExpense(**r) for r in data]
    except Exception:
        logger.warning("Could not load recurring_expenses file, starting fresh")
        return []


def _save(items: list[RecurringExpense]) -> None:
    path = Path(settings.recurring_expenses_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(r) for r in items], ensure_ascii=False),
        encoding="utf-8",
    )


def add(
    name: str,
    amount: float,
    day_of_month: int,
    month_pattern: str = "monthly",
    category: str = "חשבונות",
    payment_method: str = "לא צוין",
) -> RecurringExpense:
    items = _load()
    r = RecurringExpense(
        id=str(uuid.uuid4())[:6],
        name=name,
        amount=float(amount),
        day_of_month=int(day_of_month),
        month_pattern=month_pattern,
        category=category,
        payment_method=payment_method,
        last_inserted_date=None,
        created_at=time.time(),
    )
    items.append(r)
    _save(items)
    return r


def list_all() -> list[RecurringExpense]:
    return sorted(_load(), key=lambda r: r.day_of_month)


def remove(recurring_id: str) -> RecurringExpense | None:
    items = _load()
    for i, r in enumerate(items):
        if r.id == recurring_id:
            removed = items.pop(i)
            _save(items)
            return removed
    return None


def find_matching(identifier: str) -> list[RecurringExpense]:
    ident = identifier.strip()
    items = _load()
    by_id = [r for r in items if r.id == ident]
    if by_id:
        return by_id
    needle = ident.lower()
    return [r for r in items if needle and needle in r.name.lower()]


def _due_today(r: RecurringExpense, today: datetime) -> bool:
    """A recurring is due if today's date matches its day_of_month (clamped to the
    month's last day for short months) and this month is in month_pattern."""
    last_day = calendar.monthrange(today.year, today.month)[1]
    scheduled_day = min(r.day_of_month, last_day)
    if today.day != scheduled_day:
        return False
    if r.month_pattern == "monthly":
        return True
    allowed = [m.strip().lower() for m in r.month_pattern.split(",")]
    return _MONTH_NAMES[today.month - 1] in allowed


def insert_due_today() -> int:
    """Idempotently inserts an expense row for each recurring due today. Returns
    how many were inserted. Safe to call every tick — last_inserted_date guards."""
    today = datetime.now(_IL_TZ)
    today_str = today.strftime("%Y-%m-%d")
    items = _load()
    inserted = 0
    changed = False
    for i, r in enumerate(items):
        if r.last_inserted_date == today_str:
            continue
        if not _due_today(r, today):
            continue
        expenses_mod.add(
            sender="system",
            amount=r.amount,
            category=r.category,
            payment_method=r.payment_method,
            description=r.name,
            source="recurring",
            date=today_str,
            raw_message=f"הוצאה קבועה: {r.name}",
        )
        items[i] = replace(r, last_inserted_date=today_str)
        inserted += 1
        changed = True
        logger.info("Inserted recurring expense: %s ₪%s", r.name, r.amount)
    if changed:
        _save(items)
    return inserted
