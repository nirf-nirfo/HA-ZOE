"""Reminders store: happy path, restart, corruption, unknown keys, find_matching,
pop_due including recurring rescheduling."""
import json
import time
from pathlib import Path

import pytest

from app import reminders
from app.settings import settings


def test_empty_returns_empty_list(tmp_stores):
    assert reminders.list_reminders("s1") == []


def test_add_and_read_roundtrip(tmp_stores):
    r = reminders.add_reminder("s1", "buy milk", send_at=time.time() + 3600)
    got = reminders.list_reminders("s1")
    assert len(got) == 1
    assert got[0].id == r.id
    assert got[0].text == "buy milk"


def test_restart_preserves_state(tmp_stores):
    reminders.add_reminder("s1", "x", send_at=time.time() + 60)
    # Simulate restart: any subsequent call re-reads the file.
    assert len(reminders.list_reminders("s1")) == 1


def test_delete_happy(tmp_stores):
    r = reminders.add_reminder("s1", "x", send_at=time.time() + 60)
    assert reminders.delete_reminder(r.id, "s1") is True
    assert reminders.list_reminders("s1") == []


def test_delete_missing_is_idempotent(tmp_stores):
    assert reminders.delete_reminder("nope", "s1") is False


def test_corrupt_json_yields_empty(tmp_stores):
    Path(settings.reminders_path).write_text("{not valid json", encoding="utf-8")
    assert reminders.list_reminders("s1") == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "abc",
        "sender": "s1",
        "text": "y",
        "send_at": time.time() + 60,
        "recurrence": None,
        "future_field": "ignored",
    }
    Path(settings.reminders_path).write_text(json.dumps([row]), encoding="utf-8")
    got = reminders.list_reminders("s1")
    assert len(got) == 1
    assert got[0].id == "abc"
    assert not hasattr(got[0], "future_field")


def test_find_matching_by_id(tmp_stores):
    r = reminders.add_reminder("s1", "call doctor", send_at=time.time() + 60)
    match = reminders.find_matching("s1", r.id)
    assert [m.id for m in match] == [r.id]


def test_find_matching_by_text_substring(tmp_stores):
    r = reminders.add_reminder("s1", "call the doctor about kids", send_at=time.time() + 60)
    reminders.add_reminder("s1", "buy bread", send_at=time.time() + 60)
    match = reminders.find_matching("s1", "DOCTOR")
    assert [m.id for m in match] == [r.id]


def test_pop_due_recurring_reschedules(tmp_stores):
    past = time.time() - 60
    reminders.add_reminder("s1", "daily", send_at=past, recurrence="daily")
    due = reminders.pop_due()
    assert len(due) == 1
    # Rescheduled to a future time.
    remaining = reminders.list_reminders("s1", kind="daily")
    assert len(remaining) == 1
    assert remaining[0].send_at > time.time()


def test_pop_due_one_shot_removed(tmp_stores):
    past = time.time() - 60
    reminders.add_reminder("s1", "one", send_at=past)
    due = reminders.pop_due()
    assert len(due) == 1
    assert reminders.list_reminders("s1") == []


def test_pop_due_yearly_not_returned(tmp_stores):
    """Yearly reminders are advanced but suppressed from pop_due output
    (surface only via morning briefing)."""
    past = time.time() - 60
    reminders.add_reminder("s1", "bday", send_at=past, recurrence="yearly")
    due = reminders.pop_due()
    assert due == []
