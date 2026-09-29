import json
from pathlib import Path

from app import briefing
from app.settings import settings


def test_empty(tmp_stores):
    assert briefing.all_configs() == []
    assert briefing.get_config("s1") is None


def test_set_and_get(tmp_stores):
    c = briefing.set_config("s1", 8, 30)
    got = briefing.get_config("s1")
    assert got is not None and got.hour == 8 and got.minute == 30


def test_restart_preserves(tmp_stores):
    briefing.set_config("s1", 8, 0)
    assert briefing.get_config("s1") is not None


def test_set_updates_existing(tmp_stores):
    briefing.set_config("s1", 8, 0)
    briefing.set_config("s1", 9, 15)
    got = briefing.get_config("s1")
    assert got.hour == 9 and got.minute == 15


def test_corrupt_json(tmp_stores):
    Path(settings.briefing_config_path).write_text("garbage", encoding="utf-8")
    assert briefing.all_configs() == []


def test_legacy_json_missing_evening_fields(tmp_stores):
    """Regression for Item 01's _from_dict: a briefing_config.json written
    before evening support was added must still load (missing fields default)."""
    legacy = [{
        "sender": "s1",
        "hour": 8,
        "minute": 0,
        "enabled": True,
        "last_sent_date": "2025-01-01",
    }]
    Path(settings.briefing_config_path).write_text(json.dumps(legacy), encoding="utf-8")
    got = briefing.get_config("s1")
    assert got is not None
    assert got.evening_hour == 20  # default
    assert got.evening_enabled is True


def test_unknown_key_tolerated(tmp_stores):
    row = [{
        "sender": "s1", "hour": 8, "minute": 0, "enabled": True,
        "last_sent_date": None, "evening_hour": 20, "evening_minute": 0,
        "evening_enabled": True, "last_evening_sent_date": None,
        "future_field": "z",
    }]
    Path(settings.briefing_config_path).write_text(json.dumps(row), encoding="utf-8")
    got = briefing.get_config("s1")
    assert got is not None


def test_set_evening_creates_default_morning(tmp_stores):
    briefing.set_evening_config("s1", 21, 30)
    got = briefing.get_config("s1")
    assert got.hour == 8 and got.minute == 0  # defaults
    assert got.evening_hour == 21


def test_mark_sent_and_evening_sent(tmp_stores):
    briefing.set_config("s1", 8, 0)
    briefing.mark_sent("s1", "2026-01-01")
    briefing.mark_evening_sent("s1", "2026-01-01")
    got = briefing.get_config("s1")
    assert got.last_sent_date == "2026-01-01"
    assert got.last_evening_sent_date == "2026-01-01"


def test_all_enabled_filters(tmp_stores):
    briefing.set_config("s1", 8, 0, enabled=True)
    briefing.set_config("s2", 8, 0, enabled=False)
    briefing.set_evening_config("s2", 20, 0, enabled=False)
    got = briefing.all_enabled()
    assert {c.sender for c in got} == {"s1"}
