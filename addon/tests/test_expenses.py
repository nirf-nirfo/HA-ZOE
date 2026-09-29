import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app import expenses
from app.settings import settings

_TZ = ZoneInfo("Asia/Jerusalem")
_TODAY = datetime.now(_TZ).strftime("%Y-%m-%d")


def test_empty(tmp_stores):
    assert expenses.list_recent() == []
    s = expenses.summary(period="this_month")
    assert s["total"] == 0.0
    assert s["count"] == 0


def test_add_and_list(tmp_stores):
    e = expenses.add("s1", 42.5, "סופר", "MAX", "milk")
    got = expenses.list_recent()
    assert len(got) == 1
    assert got[0].id == e.id
    assert got[0].amount == 42.5


def test_restart_preserves(tmp_stores):
    expenses.add("s1", 10.0, "אחר", "מזומן", "x")
    assert len(expenses.list_recent()) == 1


def test_corrupt_json(tmp_stores):
    Path(settings.expenses_path).write_text("garbage", encoding="utf-8")
    assert expenses.list_recent() == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "e1", "sender": "s1", "date": _TODAY,
        "amount": 10.0, "category": "אחר", "payment_method": "מזומן",
        "description": "x", "source": "manual", "raw_message": "",
        "created_at": 0.0, "future_field": "z",
    }
    Path(settings.expenses_path).write_text(json.dumps([row]), encoding="utf-8")
    got = expenses.list_recent()
    assert len(got) == 1
    assert got[0].id == "e1"


def test_delete_last_manual_happy(tmp_stores):
    expenses.add("s1", 10.0, "אחר", "מזומן", "keep", source="recurring")
    e2 = expenses.add("s1", 20.0, "אחר", "מזומן", "take")
    removed = expenses.delete_last_manual("s1")
    assert removed is not None and removed.id == e2.id
    # Recurring one still there.
    assert len(expenses.list_recent()) == 1


def test_delete_last_manual_missing(tmp_stores):
    assert expenses.delete_last_manual("s1") is None


def test_summary_period_this_month(tmp_stores):
    expenses.add("s1", 10.0, "סופר", "MAX", "x", date=_TODAY)
    expenses.add("s1", 20.0, "דלק", "MAX", "y", date=_TODAY)
    s = expenses.summary(period="this_month")
    assert s["total"] == 30.0
    assert s["count"] == 2


def test_summary_explicit_start_end(tmp_stores):
    expenses.add("s1", 5.0, "אחר", "מזומן", "in", date="2026-01-15")
    expenses.add("s1", 5.0, "אחר", "מזומן", "out", date="2026-02-15")
    s = expenses.summary(start="2026-01-01", end="2026-01-31")
    assert s["total"] == 5.0
    assert s["count"] == 1


def test_summary_category_filter(tmp_stores):
    expenses.add("s1", 10.0, "סופר", "MAX", "x", date=_TODAY)
    expenses.add("s1", 20.0, "דלק", "MAX", "y", date=_TODAY)
    s = expenses.summary(period="this_month", category="סופר")
    assert s["total"] == 10.0


def test_summary_sender_filter(tmp_stores):
    expenses.add("s1", 10.0, "אחר", "מזומן", "a", date=_TODAY)
    expenses.add("s2", 20.0, "אחר", "מזומן", "b", date=_TODAY)
    s = expenses.summary(period="this_month", sender="s1")
    assert s["total"] == 10.0


def test_total_for_sender_on_date(tmp_stores):
    expenses.add("s1", 10.0, "אחר", "מזומן", "x", date="2026-03-01")
    expenses.add("s1", 5.0, "אחר", "מזומן", "y", date="2026-03-01", source="recurring")
    # Recurring is excluded per implementation.
    assert expenses.total_for_sender_on_date("s1", "2026-03-01") == 10.0
