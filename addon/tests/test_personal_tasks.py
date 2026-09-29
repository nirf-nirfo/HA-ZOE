import json
from pathlib import Path

from app import personal_tasks
from app.settings import settings


def test_empty(tmp_stores):
    assert personal_tasks.list_for("s1") == []


def test_add_and_read(tmp_stores):
    t = personal_tasks.add("s1", "email boss")
    got = personal_tasks.list_for("s1")
    assert len(got) == 1 and got[0].id == t.id


def test_restart_preserves(tmp_stores):
    personal_tasks.add("s1", "x")
    assert len(personal_tasks.list_for("s1")) == 1


def test_sender_isolation(tmp_stores):
    """Sender A must not see sender B's tasks."""
    personal_tasks.add("s1", "mine")
    personal_tasks.add("s2", "yours")
    assert [t.text for t in personal_tasks.list_for("s1")] == ["mine"]
    assert [t.text for t in personal_tasks.list_for("s2")] == ["yours"]


def test_complete_happy(tmp_stores):
    t = personal_tasks.add("s1", "x")
    removed = personal_tasks.complete("s1", t.id)
    assert removed is not None and removed.id == t.id
    assert personal_tasks.list_for("s1") == []


def test_complete_missing(tmp_stores):
    assert personal_tasks.complete("s1", "nope") is None


def test_corrupt_json(tmp_stores):
    Path(settings.personal_tasks_path).write_text("garbage", encoding="utf-8")
    assert personal_tasks.list_for("s1") == []


def test_unknown_key_tolerated(tmp_stores):
    data = {
        "s1": [
            {"id": "t1", "text": "x", "created_at": 0.0, "future_field": "z"},
        ],
    }
    Path(settings.personal_tasks_path).write_text(json.dumps(data), encoding="utf-8")
    got = personal_tasks.list_for("s1")
    assert len(got) == 1 and got[0].id == "t1"


def test_clear(tmp_stores):
    personal_tasks.add("s1", "a")
    personal_tasks.add("s1", "b")
    count = personal_tasks.clear("s1")
    assert count == 2
    assert personal_tasks.list_for("s1") == []


def test_find_matching(tmp_stores):
    t = personal_tasks.add("s1", "call the plumber")
    assert [m.id for m in personal_tasks.find_matching("s1", t.id)] == [t.id]
    assert [m.id for m in personal_tasks.find_matching("s1", "PLUMBER")] == [t.id]
