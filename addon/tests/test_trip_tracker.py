"""Durability tests for the per-trip effect tracker.

Covers the contract _grocery_trip_loop relies on:
- an empty tracker reports is_first_enable() True,
- seed_snapshot is a one-shot: once there are rows, re-seeding is a no-op,
- known_trip_ids returns the set we've ever stored,
- summary status + per-task progress survive a reload,
- task dedup key is stable across name whitespace variants and is unique
  per (trip, name).
"""
from app import trip_tracker


def test_empty_tracker_is_first_enable(tmp_stores):
    assert trip_tracker.is_first_enable() is True
    assert trip_tracker.known_trip_ids() == set()


def test_seed_snapshot_then_first_enable_false(tmp_stores):
    trip_tracker.seed_snapshot([("t1", "2026-11-18T09:00:00Z"), ("t2", "2026-11-18T10:00:00Z")])
    assert trip_tracker.is_first_enable() is False
    assert trip_tracker.known_trip_ids() == {"t1", "t2"}
    row = trip_tracker.get("t1")
    assert row is not None
    assert row.summary_status == "sent"
    assert row.snapshot is True


def test_seed_snapshot_is_noop_when_non_empty(tmp_stores):
    trip_tracker.mark_summary("live1", "sent", "2026-11-18T09:00:00Z")
    trip_tracker.seed_snapshot([("t1", "2026-11-18T09:00:00Z")])
    # Nothing added from the seed.
    assert trip_tracker.known_trip_ids() == {"live1"}


def test_mark_summary_updates_status(tmp_stores):
    trip_tracker.mark_summary("t1", "sent", "2026-11-18T09:00:00Z")
    row = trip_tracker.get("t1")
    assert row.summary_status == "sent"
    assert row.ended_at == "2026-11-18T09:00:00Z"


def test_mark_summary_uncertain_can_recover_to_sent(tmp_stores):
    trip_tracker.mark_summary("t1", "uncertain", "2026-11-18T09:00:00Z")
    assert trip_tracker.get("t1").summary_status == "uncertain"
    trip_tracker.mark_summary("t1", "sent", "2026-11-18T09:00:00Z")
    assert trip_tracker.get("t1").summary_status == "sent"


def test_task_done_lists_the_key_and_clears_uncertain(tmp_stores):
    key = trip_tracker.task_dedup_key("t1", "חלב")
    trip_tracker.mark_task_uncertain("t1", key)
    trip_tracker.mark_task_done("t1", key)
    row = trip_tracker.get("t1")
    assert key in row.tasks_created
    assert key not in row.tasks_uncertain


def test_task_done_is_idempotent(tmp_stores):
    key = trip_tracker.task_dedup_key("t1", "חלב")
    trip_tracker.mark_task_done("t1", key)
    trip_tracker.mark_task_done("t1", key)
    row = trip_tracker.get("t1")
    assert row.tasks_created.count(key) == 1


def test_task_uncertain_does_not_overwrite_done(tmp_stores):
    key = trip_tracker.task_dedup_key("t1", "חלב")
    trip_tracker.mark_task_done("t1", key)
    trip_tracker.mark_task_uncertain("t1", key)
    row = trip_tracker.get("t1")
    assert key in row.tasks_created
    assert key not in row.tasks_uncertain


def test_task_key_folds_whitespace_and_case(tmp_stores):
    k1 = trip_tracker.task_dedup_key("t1", "  חלב ")
    k2 = trip_tracker.task_dedup_key("t1", "חלב")
    k3 = trip_tracker.task_dedup_key("t1", "חלב 2%")
    assert k1 == k2
    assert k1 != k3


def test_task_key_unique_per_trip(tmp_stores):
    assert trip_tracker.task_dedup_key("t1", "חלב") != trip_tracker.task_dedup_key("t2", "חלב")


def test_persistence_survives_module_reload(tmp_stores):
    trip_tracker.mark_summary("t1", "sent", "2026-11-18T09:00:00Z")
    trip_tracker.mark_task_done("t1", "k1")
    # Fresh load — no in-memory state, has to hit disk.
    rows = trip_tracker._load()
    assert len(rows) == 1
    assert rows[0].summary_status == "sent"
    assert "k1" in rows[0].tasks_created
