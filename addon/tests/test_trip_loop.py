"""Integration tests for the post-shop reconciler.

Drives `trip_loop.reconcile` with a fake grocery client, fake sender, and
fake list-add functions. Verifies:
- first enable snapshots every trip and ships NO backlog,
- a later new trip sends exactly one summary + one task per unbought item,
- a second tick repeats nothing (idempotent on repeated polls),
- a trip whose task creation raised halfway through resumes without
  duplicating the already-done tasks on the next tick,
- a summary that threw records 'uncertain' and gets retried next tick,
- transport / pairing errors abort the tick with no effects,
- unbought items with blank names are skipped.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import trip_loop, trip_tracker
from app.grocery_client import (
    GroceryAPIError, GroceryPairingError, GroceryTransportError,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _trip(trip_id, ended_at, bought=None, unbought=None, paid=0, estimate=0):
    return {
        "id": trip_id,
        "endedAt": ended_at,
        "paidAgorot": paid,
        "estimateAgorot": estimate,
        "bought": bought or [],
        "unbought": unbought or [],
    }


def _deps(trips, *, add_shopping=None, add_tasks=None, send_summary=None):
    return trip_loop._Deps(
        fetch_history=AsyncMock(return_value={"trips": trips}),
        send_summary=send_summary or AsyncMock(),
        add_shopping=add_shopping or MagicMock(),
        add_tasks=add_tasks or MagicMock(),
    )


# -------- first-enable snapshot --------------------------------------------


def test_first_enable_seeds_all_trips_and_sends_nothing(tmp_stores):
    send = AsyncMock()
    add_s = MagicMock()
    add_t = MagicMock()
    deps = _deps(
        [
            _trip("t1", "2026-11-18T09:00:00Z"),
            _trip("t2", "2026-11-18T10:00:00Z"),
        ],
        send_summary=send, add_shopping=add_s, add_tasks=add_t,
    )
    out = _run(trip_loop.reconcile(deps))
    assert out == {"status": "seeded", "count": 2}
    assert send.call_count == 0
    assert add_s.call_count == 0
    assert add_t.call_count == 0
    # Tracker now knows both.
    assert trip_tracker.known_trip_ids() == {"t1", "t2"}


def test_first_enable_with_empty_history_still_marks_seeded(tmp_stores):
    deps = _deps([])
    out = _run(trip_loop.reconcile(deps))
    assert out == {"status": "seeded", "count": 0}


# -------- normal trip after seed -------------------------------------------


def test_new_trip_sends_summary_and_queues_tasks(tmp_stores):
    # Seed one trip so is_first_enable() → False.
    trip_tracker.seed_snapshot([("seeded", "2026-11-17T00:00:00Z")])
    send = AsyncMock()
    add_s = MagicMock()
    add_t = MagicMock()
    deps = _deps(
        [
            _trip("seeded", "2026-11-17T00:00:00Z"),
            _trip(
                "t_new", "2026-11-18T09:00:00Z",
                bought=[{"name": "חלב", "quantity": 2, "unitAgorot": 600}],
                unbought=[{"name": "לחם", "quantity": 1}, {"name": "ביצים", "quantity": 1}],
                paid=1200, estimate=1200,
            ),
        ],
        send_summary=send, add_shopping=add_s, add_tasks=add_t,
    )
    out = _run(trip_loop.reconcile(deps))
    assert out["summaries_sent"] == 1
    assert out["tasks_created"] == 2
    assert send.call_count == 1
    assert add_s.call_count == 2
    assert add_t.call_count == 2
    # Tracker records the trip.
    state = trip_tracker.get("t_new")
    assert state.summary_status == "sent"
    assert len(state.tasks_created) == 2


def test_second_tick_is_quiet(tmp_stores):
    trip_tracker.seed_snapshot([("seeded", "2026-11-17T00:00:00Z")])
    trips = [
        _trip(
            "t1", "2026-11-18T09:00:00Z",
            unbought=[{"name": "לחם", "quantity": 1}],
        ),
    ]
    _run(trip_loop.reconcile(_deps(trips)))
    send = AsyncMock()
    add_s = MagicMock()
    add_t = MagicMock()
    out = _run(trip_loop.reconcile(_deps(trips, send_summary=send, add_shopping=add_s, add_tasks=add_t)))
    assert out == {"status": "ok", "summaries_sent": 0, "tasks_created": 0, "uncertain": 0}
    assert send.call_count == 0
    assert add_s.call_count == 0
    assert add_t.call_count == 0


# -------- crash recovery between effects -----------------------------------


def test_crash_between_summary_and_tasks_resumes_without_duplicating(tmp_stores):
    trip_tracker.seed_snapshot([("seeded", "2026-11-17T00:00:00Z")])
    trips = [
        _trip(
            "t1", "2026-11-18T09:00:00Z",
            unbought=[
                {"name": "לחם", "quantity": 1},
                {"name": "ביצים", "quantity": 1},
                {"name": "חלב", "quantity": 1},
            ],
        ),
    ]
    # First tick: summary succeeds; first task succeeds; second raises → the
    # second gets 'uncertain', third gets 'uncertain', fourth too.
    call_log: list[str] = []

    def add_shopping_fail_after_one(text, sender):
        call_log.append(text)
        if len(call_log) > 1:
            raise RuntimeError("simulated crash mid-task")

    _run(trip_loop.reconcile(_deps(trips, add_shopping=add_shopping_fail_after_one)))
    state = trip_tracker.get("t1")
    assert state.summary_status == "sent"
    assert len(state.tasks_created) == 1
    assert len(state.tasks_uncertain) == 2

    # Second tick: summary already 'sent' so it's NOT re-sent; tasks still
    # pending get retried; already-done tasks don't.
    send = AsyncMock()
    add_s = MagicMock()
    add_t = MagicMock()
    _run(trip_loop.reconcile(_deps(trips, send_summary=send, add_shopping=add_s, add_tasks=add_t)))
    assert send.call_count == 0
    assert add_s.call_count == 2  # the two still pending
    assert add_t.call_count == 2
    state = trip_tracker.get("t1")
    assert len(state.tasks_created) == 3
    assert state.tasks_uncertain == []


def test_uncertain_summary_retries_on_next_tick(tmp_stores):
    trip_tracker.seed_snapshot([("seeded", "2026-11-17T00:00:00Z")])
    trips = [
        _trip("t1", "2026-11-18T09:00:00Z", unbought=[{"name": "לחם", "quantity": 1}]),
    ]
    failing = AsyncMock(side_effect=RuntimeError("whatsapp hiccup"))
    _run(trip_loop.reconcile(_deps(trips, send_summary=failing)))
    state = trip_tracker.get("t1")
    assert state.summary_status == "uncertain"
    # Second tick with a working send recovers.
    ok_send = AsyncMock()
    _run(trip_loop.reconcile(_deps(trips, send_summary=ok_send)))
    assert ok_send.call_count == 1
    assert trip_tracker.get("t1").summary_status == "sent"


# -------- errors + degenerate payloads -------------------------------------


def test_pairing_error_is_quiet_tick(tmp_stores):
    deps = trip_loop._Deps(
        fetch_history=AsyncMock(side_effect=GroceryPairingError("unpaired")),
        send_summary=AsyncMock(), add_shopping=MagicMock(), add_tasks=MagicMock(),
    )
    out = _run(trip_loop.reconcile(deps))
    assert out == {"status": "unpaired"}


def test_transport_error_is_quiet_tick(tmp_stores):
    deps = trip_loop._Deps(
        fetch_history=AsyncMock(side_effect=GroceryTransportError("timeout")),
        send_summary=AsyncMock(), add_shopping=MagicMock(), add_tasks=MagicMock(),
    )
    out = _run(trip_loop.reconcile(deps))
    assert out["status"] == "transport_error"


def test_api_error_surfaces_status_and_skips(tmp_stores):
    deps = trip_loop._Deps(
        fetch_history=AsyncMock(side_effect=GroceryAPIError(500, {})),
        send_summary=AsyncMock(), add_shopping=MagicMock(), add_tasks=MagicMock(),
    )
    out = _run(trip_loop.reconcile(deps))
    assert out == {"status": "api_error", "code": 500}


def test_blank_item_names_are_skipped(tmp_stores):
    trip_tracker.seed_snapshot([("seeded", "2026-11-17T00:00:00Z")])
    add_s = MagicMock()
    add_t = MagicMock()
    trips = [
        _trip("t1", "2026-11-18T09:00:00Z", unbought=[
            {"name": "", "quantity": 1},
            {"name": "   ", "quantity": 1},
            {"name": "לחם", "quantity": 1},
        ]),
    ]
    _run(trip_loop.reconcile(_deps(trips, add_shopping=add_s, add_tasks=add_t)))
    # Only the real item created.
    assert add_s.call_count == 1
    assert add_t.call_count == 1


def test_unseen_trips_processed_oldest_first(tmp_stores):
    trip_tracker.seed_snapshot([("seeded", "2026-11-17T00:00:00Z")])
    sent_order: list[str] = []

    async def record_send(text):
        sent_order.append(text)

    # Deliberately pass newer-first (matches API ordering) so the sort inside
    # reconcile has to actually run.
    trips = [
        _trip("seeded", "2026-11-17T00:00:00Z"),
        _trip("newer", "2026-11-18T11:00:00Z", unbought=[{"name": "ב", "quantity": 1}]),
        _trip("older", "2026-11-18T09:00:00Z", unbought=[{"name": "א", "quantity": 1}]),
    ]
    _run(trip_loop.reconcile(_deps(trips, send_summary=record_send)))
    # Both summaries went; the older one came first.
    assert len(sent_order) == 2
    assert "א" in sent_order[0]
    assert "ב" in sent_order[1]
