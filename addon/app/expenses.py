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

# Household-wide expense tracking (parity with the standalone שרגא bot). All
# senders share one pot; each row carries the sender's phone for attribution.
# ILS only; JSON-backed for consistency with the rest of ZOE's storage.

CATEGORIES = [
    "סופר", "מסעדות", "דלק", "חינוך", "בריאות", "ביגוד",
    "בית", "בילויים", "תחבורה", "חשבונות", "ביטוחים", "אחר",
]

PAYMENT_METHODS = [
    "MAX", "לאומי", "PayBox", "בינלאומי", "מזומן", "ביט", "לא צוין",
]


@dataclass
class Expense:
    id: str
    sender: str  # WhatsApp phone, or "system" for auto-inserted recurring
    date: str  # ISO YYYY-MM-DD
    amount: float
    category: str
    payment_method: str
    description: str
    source: str  # "manual" | "recurring" | "receipt"
    raw_message: str
    created_at: float


def _load() -> list[Expense]:
    path = Path(settings.expenses_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Expense(**e) for e in data]
    except Exception:
        logger.warning("Could not load expenses file, starting fresh")
        return []


def _save(expenses: list[Expense]) -> None:
    path = Path(settings.expenses_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(e) for e in expenses], ensure_ascii=False),
        encoding="utf-8",
    )


def add(
    sender: str,
    amount: float,
    category: str,
    payment_method: str,
    description: str,
    source: str = "manual",
    date: str | None = None,
    raw_message: str = "",
) -> Expense:
    expenses = _load()
    e = Expense(
        id=str(uuid.uuid4())[:6],
        sender=sender,
        date=date or datetime.now(_IL_TZ).strftime("%Y-%m-%d"),
        amount=float(amount),
        category=category,
        payment_method=payment_method,
        description=description,
        source=source,
        raw_message=raw_message,
        created_at=time.time(),
    )
    expenses.append(e)
    _save(expenses)
    return e


def delete_last_manual(sender: str) -> Expense | None:
    """Removes the sender's most recent manually-entered expense (not auto/recurring)."""
    expenses = _load()
    for i in range(len(expenses) - 1, -1, -1):
        if expenses[i].sender == sender and expenses[i].source in ("manual", "receipt"):
            removed = expenses.pop(i)
            _save(expenses)
            return removed
    return None


def update_last_amount(sender: str, new_amount: float) -> Expense | None:
    """Updates the amount on the sender's most recent manual expense."""
    expenses = _load()
    for i in range(len(expenses) - 1, -1, -1):
        if expenses[i].sender == sender and expenses[i].source in ("manual", "receipt"):
            expenses[i] = replace(expenses[i], amount=float(new_amount))
            _save(expenses)
            return expenses[i]
    return None


def list_recent(limit: int = 10, sender: str | None = None) -> list[Expense]:
    expenses = _load()
    if sender:
        expenses = [e for e in expenses if e.sender == sender]
    return sorted(expenses, key=lambda e: e.created_at, reverse=True)[:limit]


def _period_bounds(period: str) -> tuple[str, str] | None:
    """Returns (start, end) ISO dates (inclusive) for a period keyword, or None."""
    now = datetime.now(_IL_TZ)
    today = now.strftime("%Y-%m-%d")
    if period == "today":
        return today, today
    if period == "this_week":
        # ISO week starts Monday; Israeli week starts Sunday. Use Sunday.
        weekday = (now.weekday() + 1) % 7  # 0=Sunday
        start = (now - timedelta(days=weekday)).strftime("%Y-%m-%d")
        return start, today
    if period == "this_month":
        return now.strftime("%Y-%m-01"), today
    if period == "last_month":
        first_this = now.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.strftime("%Y-%m-01"), last_prev.strftime("%Y-%m-%d")
    if period == "this_year":
        return now.strftime("%Y-01-01"), today
    return None


def summary(
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
    sender: str | None = None,
) -> dict:
    """Aggregates expenses in a date range. Either `period` OR explicit start+end."""
    if period:
        bounds = _period_bounds(period)
        if bounds:
            start, end = bounds
    if not start or not end:
        # Default to this month.
        start, end = _period_bounds("this_month")

    expenses = [
        e for e in _load()
        if start <= e.date <= end
        and (category is None or e.category == category)
        and (sender is None or e.sender == sender)
    ]

    by_sender: dict[str, float] = {}
    by_category: dict[str, float] = {}
    by_payment: dict[str, float] = {}
    total = 0.0
    for e in expenses:
        total += e.amount
        by_sender[e.sender] = by_sender.get(e.sender, 0.0) + e.amount
        by_category[e.category] = by_category.get(e.category, 0.0) + e.amount
        by_payment[e.payment_method] = by_payment.get(e.payment_method, 0.0) + e.amount

    return {
        "start": start,
        "end": end,
        "total": round(total, 2),
        "count": len(expenses),
        "by_sender": {k: round(v, 2) for k, v in by_sender.items()},
        "by_category": dict(sorted(
            ((k, round(v, 2)) for k, v in by_category.items()),
            key=lambda kv: kv[1], reverse=True,
        )),
        "by_payment_method": {k: round(v, 2) for k, v in by_payment.items()},
    }


def total_for_sender_on_date(sender: str, date: str) -> float:
    """Sum of a sender's manual/receipt expenses on `date` — used by the evening briefing."""
    return round(
        sum(
            e.amount
            for e in _load()
            if e.sender == sender and e.date == date and e.source in ("manual", "receipt")
        ),
        2,
    )
