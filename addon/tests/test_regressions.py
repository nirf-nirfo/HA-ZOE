"""Regression tests for Item 01 fixes plus the defensive-load pattern.

Each `_from_dict` in `addon/app/*.py` filters out unknown keys so a JSON row
saved by a future add-on version doesn't kill the load. This test locks that
behavior in for every store that has a `_from_dict`.
"""
from pathlib import Path

import pytest

from app import (
    agenda,
    anchors,
    briefing,
    check_ins,
    expenses,
    lists,
    memory,
    monitors,
    personal_tasks,
    recurring_expenses,
    reminders,
    scheduled_actions,
)


# Minimum valid dict per store, matched to that store's dataclass. `_from_dict`
# should drop the extra `future_field` and return a usable dataclass instance.
_STORE_ROWS = [
    (
        "reminders",
        reminders._from_dict,
        {"id": "a", "sender": "s1", "text": "x", "send_at": 1.0, "recurrence": None},
    ),
    (
        "agenda",
        agenda._from_dict,
        {"id": "a", "sender": "s1", "date": "2026-01-01", "text": "x", "added_at": 1.0},
    ),
    (
        "anchors",
        anchors._anchor_from_dict,
        {"id": "a", "day": "sunday", "text": "x", "time": "09:00", "added_at": 1.0},
    ),
    (
        "briefing",
        briefing._from_dict,
        {"sender": "s1", "hour": 7, "minute": 0},
    ),
    (
        "check_ins",
        check_ins._from_dict,
        {"id": "a", "sender": "s1", "prompt": "x", "next_at": 1.0},
    ),
    (
        "expenses",
        expenses._from_dict,
        {
            "id": "a",
            "sender": "s1",
            "date": "2026-01-01",
            "amount": 10.0,
            "category": "אחר",
            "payment_method": "לא צוין",
            "description": "",
            "source": "manual",
            "raw_message": "",
            "created_at": 1.0,
        },
    ),
    (
        "lists",
        lists._from_dict,
        {"id": "a", "text": "x", "added_by": "s1", "added_at": 1.0},
    ),
    (
        "memory",
        memory._from_dict,
        {"id": "a", "text": "x", "added_at": 1.0},
    ),
    (
        "monitors",
        monitors._from_dict,
        {
            "id": "a",
            "sender": "s1",
            "entity_id": "light.kitchen",
            "entity_name": "kitchen",
            "expected_state": "off",
            "alert_text": "on!",
            "interval_minutes": 5.0,
            "next_check": 1.0,
            "until": 2.0,
        },
    ),
    (
        "personal_tasks",
        personal_tasks._from_dict,
        {"id": "a", "text": "x", "created_at": 1.0},
    ),
    (
        "recurring_expenses",
        recurring_expenses._from_dict,
        {
            "id": "a",
            "name": "rent",
            "amount": 100.0,
            "day_of_month": 1,
            "month_pattern": "monthly",
            "category": "חשבונות",
            "payment_method": "לא צוין",
            "last_inserted_date": None,
            "created_at": 1.0,
        },
    ),
    (
        "scheduled_actions",
        scheduled_actions._from_dict,
        {
            "id": "a",
            "sender": "s1",
            "entity_id": "light.kitchen",
            "entity_name": "kitchen",
            "domain": "light",
            "service": "turn_on",
            "service_data": {},
            "description": "",
            "run_at": 1.0,
        },
    ),
]


@pytest.mark.parametrize("name,from_dict,row", _STORE_ROWS, ids=[t[0] for t in _STORE_ROWS])
def test_from_dict_tolerates_unknown_key(name, from_dict, row):
    """Regression: Item 01's defensive `_from_dict` filters unknown fields."""
    with_extra = dict(row, future_field="ignored")
    obj = from_dict(with_extra)
    # Object was built successfully.
    assert obj is not None
    # And the extra field did not sneak onto the dataclass instance.
    assert not hasattr(obj, "future_field")


# ---------- Expense handler: source no longer has the dead `receipt` branch --


def test_handle_expense_call_source_line_has_no_dead_receipt_branch():
    """Item 01 fix: `_handle_expense_call` used to compute `source` with a
    dead `"receipt" if ... else "manual"` line and then overwrite it. The
    fixed version just uses inp.get("source") or "manual". Guard against a
    regression that restores the dead pattern.

    Read the file directly so this test doesn't have to import `app.main`
    (which pulls in FastAPI + the whole agent loop for a text check)."""
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    src = main_path.read_text(encoding="utf-8")
    # The rewritten line is present:
    assert 'inp.get("source") or "manual"' in src
    # And the dead conditional isn't:
    assert '"receipt" if' not in src


# ---------- check_ins legacy row (Item 01 defense in check_ins._load) --------


def test_check_ins_load_tolerates_legacy_row_without_interval_minutes(tmp_stores):
    """`interval_minutes` was added later; older JSON rows omit it and must
    still load. Covered from the store surface (not just _from_dict) so a
    future _load rewrite that bypasses _from_dict still gets caught."""
    import json
    import time as _time
    from pathlib import Path

    from app.settings import settings as _settings

    row = {
        "id": "c1",
        "sender": "s1",
        "prompt": "x",
        "next_at": _time.time() + 60,
        "recurrence": None,
        # interval_minutes intentionally missing
    }
    Path(_settings.check_ins_path).write_text(json.dumps([row]), encoding="utf-8")
    got = check_ins.list_for_sender("s1")
    assert len(got) == 1
    assert got[0].interval_minutes is None
