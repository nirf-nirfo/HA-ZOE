import json
from pathlib import Path

from app import anchors
from app.settings import settings


def test_empty(tmp_stores):
    assert anchors.list_all() == []


def test_add_and_read(tmp_stores):
    a = anchors.add_anchor("sunday", "school ends", "13:00")
    got = anchors.list_all()
    assert len(got) == 1
    assert got[0].id == a.id
    assert got[0].day == "sunday"


def test_restart_preserves(tmp_stores):
    anchors.add_anchor("sunday", "x", None)
    assert len(anchors.list_all()) == 1


def test_remove_happy(tmp_stores):
    a = anchors.add_anchor("monday", "x", None)
    assert anchors.remove_anchor(a.id) is True
    assert anchors.list_all() == []


def test_remove_missing_idempotent(tmp_stores):
    assert anchors.remove_anchor("nope") is False


def test_corrupt_json(tmp_stores):
    Path(settings.anchors_path).write_text("not json", encoding="utf-8")
    assert anchors.list_all() == []


def test_unknown_key_tolerated(tmp_stores):
    payload = {
        "anchors": [
            {"id": "a1", "day": "sunday", "text": "x", "time": None,
             "added_at": 0.0, "future_field": "z"},
        ],
        "suppressions": [
            {"anchor_id": "a1", "date": "2026-01-01", "future_field": "z"},
        ],
    }
    Path(settings.anchors_path).write_text(json.dumps(payload), encoding="utf-8")
    got = anchors.list_all()
    assert len(got) == 1
    assert got[0].id == "a1"


def test_anchors_for_date_filters_suppressed(tmp_stores):
    a = anchors.add_anchor("sunday", "soccer", "16:00")
    anchors.add_anchor("sunday", "school pickup", "13:00")
    anchors.suppress_for_date(a.id, "2026-01-04")
    got = anchors.anchors_for_date("sunday", "2026-01-04")
    assert [x.text for x in got] == ["school pickup"]


def test_suppress_missing_anchor_returns_false(tmp_stores):
    assert anchors.suppress_for_date("nope", "2026-01-01") is False


def test_purge_suppressions_before(tmp_stores):
    a = anchors.add_anchor("sunday", "x", None)
    anchors.suppress_for_date(a.id, "2025-01-01")
    anchors.suppress_for_date(a.id, "2026-01-01")
    removed = anchors.purge_suppressions_before("2026-01-01")
    assert removed == 1


def test_find_matching_id_and_text(tmp_stores):
    a = anchors.add_anchor("monday", "gym class", None)
    assert [m.id for m in anchors.find_matching(a.id)] == [a.id]
    assert [m.id for m in anchors.find_matching("GYM")] == [a.id]
