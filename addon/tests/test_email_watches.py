"""Item 21: store-level tests for email_watches.

Covers the CRUD surface, the rolling-cap behaviour on `last_processed_uids`,
and the two flag flips (enable / disable). The watch LOOP's semantics live in
test_email_watch_loop.py; the Claude tool handlers live in
test_email_watch_tools.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app import email_watches
from app.settings import settings


def test_empty(tmp_stores):
    assert email_watches.list_watches() == []
    assert email_watches.count_all() == 0
    assert email_watches.count_enabled() == 0


def test_add_sets_defaults(tmp_stores):
    w = email_watches.add(name="shop receipts", from_contains="receipt@shop.co.il")
    assert w.name == "shop receipts"
    assert w.from_contains == "receipt@shop.co.il"
    assert w.subject_contains == ""
    assert w.body_contains == ""
    assert w.interval_minutes == 60
    assert w.enabled is True
    assert w.last_checked_at is None
    assert w.last_processed_uids == []
    assert email_watches.count_all() == 1
    assert email_watches.count_enabled() == 1


def test_add_rejects_sub_minute_interval(tmp_stores):
    # Floor at 1 min; a 0-minute interval would make the loop fire the watch
    # every tick and effectively DoS the IMAP server.
    w = email_watches.add(name="x", from_contains="a", interval_minutes=0)
    assert w.interval_minutes == 1


def test_list_order_preserves_insertion(tmp_stores):
    a = email_watches.add(name="a", from_contains="a@x")
    b = email_watches.add(name="b", from_contains="b@x")
    ids = [w.id for w in email_watches.list_watches()]
    assert ids == [a.id, b.id]


def test_restart_preserves(tmp_stores):
    email_watches.add(name="x", subject_contains="receipt")
    # Simulated restart: fresh load from the same path yields the same row.
    assert len(email_watches.list_watches()) == 1


def test_delete_happy(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    assert email_watches.delete(w.id) is True
    assert email_watches.list_watches() == []


def test_delete_missing(tmp_stores):
    assert email_watches.delete("nope") is False


def test_enable_disable(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    assert email_watches.disable(w.id) is True
    assert email_watches.get(w.id).enabled is False
    assert email_watches.count_enabled() == 0
    assert email_watches.enable(w.id) is True
    assert email_watches.get(w.id).enabled is True
    assert email_watches.count_enabled() == 1


def test_enable_disable_unknown_id(tmp_stores):
    assert email_watches.enable("nope") is False
    assert email_watches.disable("nope") is False


def test_update_last_checked_roundtrips(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    email_watches.update_last_checked(w.id, now)
    # Re-load from disk to prove the datetime survives JSON ↔ dataclass.
    reloaded = email_watches.get(w.id)
    assert reloaded.last_checked_at == now


def test_update_last_checked_unknown_is_noop(tmp_stores):
    # Must not raise, and must not create a ghost row.
    email_watches.update_last_checked("nope", datetime.now(timezone.utc))
    assert email_watches.list_watches() == []


def test_mark_uids_processed_appends_in_order(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    email_watches.mark_uids_processed(w.id, ["1", "2", "3"])
    email_watches.mark_uids_processed(w.id, ["4"])
    assert email_watches.get(w.id).last_processed_uids == ["1", "2", "3", "4"]


def test_mark_uids_processed_dedupes(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    email_watches.mark_uids_processed(w.id, ["1", "2"])
    email_watches.mark_uids_processed(w.id, ["2", "3", "3"])
    uids = email_watches.get(w.id).last_processed_uids
    assert uids == ["1", "2", "3"]


def test_mark_uids_processed_rolling_cap(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    # Push 150 unique uids and verify the oldest 50 drop out.
    email_watches.mark_uids_processed(w.id, [f"u{i}" for i in range(150)])
    uids = email_watches.get(w.id).last_processed_uids
    assert len(uids) == 100
    assert uids[0] == "u50"
    assert uids[-1] == "u149"


def test_mark_uids_processed_empty_noop(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    email_watches.mark_uids_processed(w.id, [])
    assert email_watches.get(w.id).last_processed_uids == []


def test_mark_uids_processed_unknown_id_noop(tmp_stores):
    # Watch may have been deleted mid-tick; appending must not resurrect it.
    email_watches.mark_uids_processed("nope", ["1"])
    assert email_watches.list_watches() == []


def test_corrupt_json(tmp_stores):
    Path(settings.email_watches_path).write_text("garbage", encoding="utf-8")
    assert email_watches.list_watches() == []


def test_unknown_key_tolerated(tmp_stores):
    row = {
        "id": "w1",
        "name": "legacy",
        "from_contains": "a@x",
        "subject_contains": "",
        "body_contains": "",
        "interval_minutes": 60,
        "last_checked_at": None,
        "last_processed_uids": [],
        "enabled": True,
        "future_field": "z",  # must not break load
    }
    Path(settings.email_watches_path).write_text(
        json.dumps([row]), encoding="utf-8"
    )
    got = email_watches.list_watches()
    assert len(got) == 1
    assert got[0].id == "w1"


def test_legacy_row_without_enabled_defaults_to_true(tmp_stores):
    """Store-base drops unknown keys and backfills missing ones from the
    dataclass defaults, so an older row survives a schema addition."""
    row = {
        "id": "w1",
        "name": "legacy",
        "from_contains": "a@x",
        "interval_minutes": 60,
        "last_processed_uids": [],
        # enabled / subject_contains / body_contains intentionally missing
    }
    Path(settings.email_watches_path).write_text(
        json.dumps([row]), encoding="utf-8"
    )
    got = email_watches.list_watches()
    assert len(got) == 1
    assert got[0].enabled is True
    assert got[0].subject_contains == ""
    assert got[0].body_contains == ""
