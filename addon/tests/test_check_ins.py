import json
import time
from pathlib import Path

from app import check_ins
from app.settings import settings


def test_empty(tmp_stores):
    assert check_ins.list_for_sender("s1") == []


def test_add_and_read(tmp_stores):
    c = check_ins.add("s1", "check gym", next_at=time.time() + 3600)
    got = check_ins.list_for_sender("s1")
    assert len(got) == 1
    assert got[0].id == c.id


def test_restart_preserves(tmp_stores):
    check_ins.add("s1", "x", next_at=time.time() + 60)
    assert len(check_ins.list_for_sender("s1")) == 1


def test_remove_happy(tmp_stores):
    c = check_ins.add("s1", "x", next_at=time.time() + 60)
    assert check_ins.remove(c.id, "s1") is True
    assert check_ins.list_for_sender("s1") == []


def test_remove_missing_idempotent(tmp_stores):
    assert check_ins.remove("nope", "s1") is False


def test_corrupt_json(tmp_stores):
    Path(settings.check_ins_path).write_text("garbage", encoding="utf-8")
    assert check_ins.list_for_sender("s1") == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "c1",
        "sender": "s1",
        "prompt": "x",
        "next_at": time.time() + 60,
        "recurrence": None,
        "interval_minutes": None,
        "future_field": "z",
    }
    Path(settings.check_ins_path).write_text(json.dumps([row]), encoding="utf-8")
    got = check_ins.list_for_sender("s1")
    assert len(got) == 1
    assert got[0].id == "c1"


def test_legacy_row_without_interval_minutes(tmp_stores):
    """Regression: pre-interval_minutes JSON rows must still load."""
    row = {
        "id": "c1",
        "sender": "s1",
        "prompt": "x",
        "next_at": time.time() + 60,
        "recurrence": None,
        # interval_minutes intentionally missing
    }
    Path(settings.check_ins_path).write_text(json.dumps([row]), encoding="utf-8")
    got = check_ins.list_for_sender("s1")
    assert len(got) == 1
    assert got[0].interval_minutes is None


def test_pop_due_interval_reschedules(tmp_stores):
    past = time.time() - 60
    c = check_ins.add("s1", "poll", next_at=past, interval_minutes=15)
    due = check_ins.pop_due()
    assert len(due) == 1
    # Rescheduled forward.
    remaining = check_ins.list_for_sender("s1")
    assert len(remaining) == 1
    assert remaining[0].next_at > time.time()
    assert remaining[0].id == c.id


def test_pop_due_recurrence_reschedules(tmp_stores):
    past = time.time() - 60
    check_ins.add("s1", "daily", next_at=past, recurrence="daily")
    due = check_ins.pop_due()
    assert len(due) == 1
    remaining = check_ins.list_for_sender("s1")
    assert len(remaining) == 1
    assert remaining[0].next_at > time.time()


def test_pop_due_one_shot_removed(tmp_stores):
    past = time.time() - 60
    check_ins.add("s1", "once", next_at=past)
    due = check_ins.pop_due()
    assert len(due) == 1
    assert check_ins.list_for_sender("s1") == []


def test_find_matching(tmp_stores):
    c = check_ins.add("s1", "check on Anna's homework", next_at=time.time() + 60)
    assert [m.id for m in check_ins.find_matching("s1", c.id)] == [c.id]
    assert [m.id for m in check_ins.find_matching("s1", "HOMEWORK")] == [c.id]
