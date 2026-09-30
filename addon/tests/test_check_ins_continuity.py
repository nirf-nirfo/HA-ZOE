"""Item 12: check-in continuity across ticks.

Covers the new `last_fired_at` / `last_fired_text` fields on `CheckIn`, the
`record_fire` helper, and legacy-row tolerance. Model-facing behavior (the
framing extension) lives in the behavior harness, not here.
"""
import json
import time
from pathlib import Path

from app import check_ins
from app.settings import settings


def test_new_fields_default_none(tmp_stores):
    c = check_ins.add("s1", "poll", next_at=time.time() + 60)
    assert c.last_fired_at is None
    assert c.last_fired_text is None


def test_record_fire_updates_target(tmp_stores):
    c = check_ins.add("s1", "poll", next_at=time.time() + 60)
    before = time.time()
    check_ins.record_fire(c.id, "How's the report coming?")
    rows = check_ins._load()
    updated = next(r for r in rows if r.id == c.id)
    assert updated.last_fired_text == "How's the report coming?"
    assert updated.last_fired_at is not None
    assert updated.last_fired_at >= before


def test_record_fire_only_touches_matching_id(tmp_stores):
    a = check_ins.add("s1", "a", next_at=time.time() + 60)
    b = check_ins.add("s1", "b", next_at=time.time() + 60)
    check_ins.record_fire(a.id, "hi")
    rows = {r.id: r for r in check_ins._load()}
    assert rows[a.id].last_fired_text == "hi"
    assert rows[b.id].last_fired_text is None
    assert rows[b.id].last_fired_at is None


def test_record_fire_missing_id_idempotent(tmp_stores):
    # No rows at all — must not raise.
    check_ins.record_fire("nope", "hi")
    assert check_ins._load() == []
    # And with an unrelated row present, still no crash and no mutation.
    c = check_ins.add("s1", "poll", next_at=time.time() + 60)
    check_ins.record_fire("nope", "hi")
    rows = check_ins._load()
    assert len(rows) == 1
    assert rows[0].id == c.id
    assert rows[0].last_fired_text is None


def test_legacy_row_without_continuity_fields_loads(tmp_stores):
    """Regression: pre-Item-12 JSON rows must still load with the new fields
    defaulted to None (unknown-key filtering in Store._from_dict)."""
    future = time.time() + 60
    row = {
        "id": "c1",
        "sender": "s1",
        "prompt": "x",
        "next_at": future,
        "recurrence": None,
        "interval_minutes": None,
        # last_fired_at / last_fired_text intentionally missing
    }
    Path(settings.check_ins_path).write_text(json.dumps([row]), encoding="utf-8")
    got = check_ins.list_for_sender("s1")
    assert len(got) == 1
    assert got[0].last_fired_at is None
    assert got[0].last_fired_text is None


def test_record_fire_survives_reload(tmp_stores):
    c = check_ins.add("s1", "poll", next_at=time.time() + 60)
    check_ins.record_fire(c.id, "ping one")
    # Re-read from disk to prove save happened.
    rows = check_ins._load()
    assert rows[0].last_fired_text == "ping one"
