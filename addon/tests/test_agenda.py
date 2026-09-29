import json
from pathlib import Path

from app import agenda
from app.settings import settings


def test_empty(tmp_stores):
    assert agenda.items_for_date("s1", "2026-01-01") == []


def test_add_and_read(tmp_stores):
    a = agenda.add_item("s1", "2026-01-01", "meeting")
    got = agenda.items_for_date("s1", "2026-01-01")
    assert len(got) == 1
    assert got[0].id == a.id


def test_restart_preserves(tmp_stores):
    agenda.add_item("s1", "2026-01-01", "x")
    assert len(agenda.items_for_date("s1", "2026-01-01")) == 1


def test_remove_happy(tmp_stores):
    agenda.add_item("s1", "2026-01-01", "buy tickets")
    removed = agenda.remove_item("s1", "2026-01-01", "tickets")
    assert removed == ["buy tickets"]
    assert agenda.items_for_date("s1", "2026-01-01") == []


def test_remove_missing_is_idempotent(tmp_stores):
    assert agenda.remove_item("s1", "2026-01-01", "nothing") == []


def test_corrupt_json(tmp_stores):
    Path(settings.agenda_path).write_text("garbage", encoding="utf-8")
    assert agenda.items_for_date("s1", "2026-01-01") == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "a1",
        "sender": "s1",
        "date": "2026-01-01",
        "text": "x",
        "added_at": 0.0,
        "future_field": "z",
    }
    Path(settings.agenda_path).write_text(json.dumps([row]), encoding="utf-8")
    got = agenda.items_for_date("s1", "2026-01-01")
    assert len(got) == 1
    assert got[0].id == "a1"


def test_purge_before(tmp_stores):
    agenda.add_item("s1", "2025-06-01", "old")
    agenda.add_item("s1", "2026-06-01", "new")
    removed = agenda.purge_before("2026-01-01")
    assert removed == 1
    assert len(agenda.items_for_date("s1", "2026-06-01")) == 1
