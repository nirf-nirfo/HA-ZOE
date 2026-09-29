import json
import time
from pathlib import Path

from app import monitors
from app.settings import settings


def _mk(sender="s1", **kw):
    defaults = dict(
        sender=sender, entity_id="light.a", entity_name="Living",
        expected_state="off", alert_text="oops",
        interval_minutes=5.0, until=time.time() + 3600,
    )
    defaults.update(kw)
    return monitors.add_monitor(**defaults)


def test_empty(tmp_stores):
    assert monitors.list_monitors("s1") == []


def test_add_and_list(tmp_stores):
    m = _mk()
    got = monitors.list_monitors("s1")
    assert len(got) == 1 and got[0].id == m.id


def test_restart_preserves(tmp_stores):
    _mk()
    assert len(monitors.list_monitors("s1")) == 1


def test_delete_happy(tmp_stores):
    m = _mk()
    assert monitors.delete_monitor(m.id, "s1") is True
    assert monitors.list_monitors("s1") == []


def test_delete_missing(tmp_stores):
    assert monitors.delete_monitor("nope", "s1") is False


def test_corrupt_json(tmp_stores):
    Path(settings.monitors_path).write_text("garbage", encoding="utf-8")
    assert monitors.list_monitors("s1") == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "m1", "sender": "s1", "entity_id": "light.a",
        "entity_name": "Living", "expected_state": "off",
        "alert_text": "x", "interval_minutes": 5.0,
        "next_check": time.time(), "until": time.time() + 3600,
        "last_state": None, "future_field": "z",
    }
    Path(settings.monitors_path).write_text(json.dumps([row]), encoding="utf-8")
    got = monitors.list_monitors("s1")
    assert len(got) == 1


def test_advance_updates_state(tmp_stores):
    m = _mk()
    next_check = time.time() + 300
    monitors.advance(m.id, next_check, last_state="on")
    got = monitors.list_monitors("s1")[0]
    assert got.next_check == next_check
    assert got.last_state == "on"


def test_purge_expired(tmp_stores):
    now = time.time()
    _mk(until=now - 60)  # expired
    _mk(until=now + 3600)  # active
    removed = monitors.purge_expired(now)
    assert removed == 1
    assert len(monitors.list_monitors("s1")) == 1


def test_get_due_only_active(tmp_stores):
    now = time.time()
    m1 = _mk(until=now + 3600)
    # next_check is set to now on creation, so it's due immediately.
    due = monitors.get_due(now + 1)
    assert [d.id for d in due] == [m1.id]
