import json
import time
from pathlib import Path

from app import scheduled_actions
from app.settings import settings


def _mk(sender="s1", **kw):
    defaults = dict(
        sender=sender, entity_id="light.a", entity_name="Living",
        domain="light", service="turn_on", service_data={},
        description="turn on living", run_at=time.time() + 3600,
    )
    defaults.update(kw)
    return scheduled_actions.add_action(**defaults)


def test_empty(tmp_stores):
    assert scheduled_actions.list_actions("s1") == []


def test_add_and_list(tmp_stores):
    a = _mk()
    got = scheduled_actions.list_actions("s1")
    assert len(got) == 1 and got[0].id == a.id


def test_restart_preserves(tmp_stores):
    _mk()
    assert len(scheduled_actions.list_actions("s1")) == 1


def test_delete_happy(tmp_stores):
    a = _mk()
    assert scheduled_actions.delete_action(a.id, "s1") is True


def test_delete_missing(tmp_stores):
    assert scheduled_actions.delete_action("nope", "s1") is False


def test_corrupt_json(tmp_stores):
    Path(settings.scheduled_actions_path).write_text("garbage", encoding="utf-8")
    assert scheduled_actions.list_actions("s1") == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "a1", "sender": "s1", "entity_id": "light.a",
        "entity_name": "Living", "domain": "light", "service": "turn_on",
        "service_data": {}, "description": "x",
        "run_at": time.time() + 60, "duration_minutes": None,
        "future_field": "z",
    }
    Path(settings.scheduled_actions_path).write_text(json.dumps([row]), encoding="utf-8")
    got = scheduled_actions.list_actions("s1")
    assert len(got) == 1


def test_pop_due_removes(tmp_stores):
    _mk(run_at=time.time() - 60)
    due = scheduled_actions.pop_due()
    assert len(due) == 1
    assert scheduled_actions.list_actions("s1") == []


def test_find_matching(tmp_stores):
    a = _mk(description="close all shutters at bedtime")
    assert [x.id for x in scheduled_actions.find_matching("s1", a.id)] == [a.id]
    assert [x.id for x in scheduled_actions.find_matching("s1", "shutters")] == [a.id]


def test_find_duplicate(tmp_stores):
    a = _mk(run_at=1000.0)
    dup = scheduled_actions.find_duplicate("s1", "light.a", "turn_on", {}, 1000.0)
    assert dup is not None and dup.id == a.id
    assert scheduled_actions.find_duplicate("s1", "light.a", "turn_on", {}, 2000.0) is None
