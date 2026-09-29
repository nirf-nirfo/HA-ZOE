import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from app import expenses, recurring_expenses
from app.settings import settings

_TZ = ZoneInfo("Asia/Jerusalem")


def test_empty(tmp_stores):
    assert recurring_expenses.list_all() == []


def test_add_and_list(tmp_stores):
    r = recurring_expenses.add("rent", 5000.0, day_of_month=1)
    got = recurring_expenses.list_all()
    assert len(got) == 1 and got[0].id == r.id


def test_restart_preserves(tmp_stores):
    recurring_expenses.add("x", 10.0, day_of_month=1)
    assert len(recurring_expenses.list_all()) == 1


def test_remove(tmp_stores):
    r = recurring_expenses.add("x", 10.0, day_of_month=1)
    assert recurring_expenses.remove(r.id) is not None
    assert recurring_expenses.list_all() == []


def test_remove_missing(tmp_stores):
    assert recurring_expenses.remove("nope") is None


def test_corrupt_json(tmp_stores):
    Path(settings.recurring_expenses_path).write_text("garbage", encoding="utf-8")
    assert recurring_expenses.list_all() == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "r1", "name": "rent", "amount": 5000.0, "day_of_month": 1,
        "month_pattern": "monthly", "category": "חשבונות",
        "payment_method": "לא צוין", "last_inserted_date": None,
        "created_at": 0.0, "future_field": "z",
    }
    Path(settings.recurring_expenses_path).write_text(json.dumps([row]), encoding="utf-8")
    got = recurring_expenses.list_all()
    assert len(got) == 1 and got[0].name == "rent"


def test_due_today_short_month_clamp_feb(tmp_stores):
    """Feb 30 -> clamped to Feb 28 (non-leap) / 29 (leap)."""
    r = recurring_expenses.add("rent", 100.0, day_of_month=30)
    stored = recurring_expenses.list_all()[0]
    feb28_nonleap = datetime(2026, 2, 28, tzinfo=_TZ)
    feb29_leap = datetime(2028, 2, 29, tzinfo=_TZ)
    feb27 = datetime(2026, 2, 27, tzinfo=_TZ)
    assert recurring_expenses._due_today(stored, feb28_nonleap) is True
    assert recurring_expenses._due_today(stored, feb29_leap) is True
    assert recurring_expenses._due_today(stored, feb27) is False


def test_due_today_month_pattern_filter(tmp_stores):
    r = recurring_expenses.add("semi", 100.0, day_of_month=1, month_pattern="jan,jul")
    stored = recurring_expenses.list_all()[0]
    jan1 = datetime(2026, 1, 1, tzinfo=_TZ)
    feb1 = datetime(2026, 2, 1, tzinfo=_TZ)
    jul1 = datetime(2026, 7, 1, tzinfo=_TZ)
    assert recurring_expenses._due_today(stored, jan1) is True
    assert recurring_expenses._due_today(stored, feb1) is False
    assert recurring_expenses._due_today(stored, jul1) is True


def test_insert_due_today_idempotent(tmp_stores):
    """Same day twice = one insert (last_inserted_date guard)."""
    today = datetime.now(_TZ)
    recurring_expenses.add("rent", 5000.0, day_of_month=today.day)
    n1 = recurring_expenses.insert_due_today()
    n2 = recurring_expenses.insert_due_today()
    assert n1 == 1
    assert n2 == 0
    assert len(expenses.list_recent()) == 1
