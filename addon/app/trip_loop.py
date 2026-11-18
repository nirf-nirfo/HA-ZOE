"""Item 25: the post-shop reconcile step.

Called from `loops._grocery_trip_loop` on a 5-minute cadence. Isolated out
of loops.py so the logic is independently testable with a mock client and
a tmp tracker — loops.py stays the thin while/sleep wrapper.

Flow per tick:

  1. GET /api/zoe/history. Source of truth for the full set of trips,
     covers downtime, survives if /summary isn't reachable.
  2. If the tracker is empty (first-ever enable), seed every current trip
     id into the tracker as "already processed, snapshot" so the household's
     back-catalog never gets replayed. Return — no effects this tick.
  3. Order unprocessed trips oldest-first by `endedAt` and process each in
     turn:
       a. Send the summary to all allowed senders (best effort). Record
          `summary_status` as 'sent' on success, 'uncertain' on a transport
          hiccup — a later tick will re-check history and retry only the
          uncertain ones.
       b. For each unbought item, add_to_list('shopping') + add_to_list('tasks')
          using a stable dedup key per (trip, name). A restart mid-trip
          resumes from the recorded keys — items already created don't
          repeat. Create failures get an 'uncertain' marker instead.
  4. Return.

Effects are applied in the order summary → tasks so a crash between them
is bounded: summary 'sent' + tasks partial = we resume tasks; summary
'uncertain' + tasks 0 = we re-check history before trying again.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app import lists, trip_summary, trip_tracker
from app.grocery_client import (
    GroceryAPIError, GroceryPairingError, GroceryTransportError,
)
from app.logging_config import logger

# Phone used as `added_by` on shopping-list and tasks-list rows the loop
# creates. It isn't a real sender — any lookup through senders.resolve will
# fall back to this literal, which is what we want ("Zoe added it for you").
_LOOP_AUTHOR = "zoe-trip-loop"


@dataclass
class _Deps:
    """The I/O surface the loop uses. All real-world side effects go through
    one of these — tests pass fakes to drive every branch without network,
    without WhatsApp, and without touching the household's real list."""
    fetch_history: Callable[[], Any]  # async: () -> dict
    send_summary: Callable[[str], Any]  # async: (text) -> None  (broadcast)
    add_shopping: Callable[[str, str], Any]  # sync: (text, sender) -> row
    add_tasks: Callable[[str, str], Any]  # sync: (text, sender) -> row


async def reconcile(deps: _Deps) -> dict:
    """Runs one reconcile pass. Returns a dict summarising what happened —
    the loop logs it so a tail of the add-on log tells Nir which trips moved."""
    try:
        history = await deps.fetch_history()
    except GroceryPairingError:
        logger.warning("Trip loop: grocery not paired; skipping tick")
        return {"status": "unpaired"}
    except GroceryTransportError as exc:
        logger.warning("Trip loop: transport error on /history: %s", exc)
        return {"status": "transport_error"}
    except GroceryAPIError as exc:
        logger.warning("Trip loop: /history returned %s", exc.status)
        return {"status": "api_error", "code": exc.status}

    trips = history.get("trips") or []
    if not isinstance(trips, list):
        logger.warning("Trip loop: /history 'trips' is not a list; skipping")
        return {"status": "bad_payload"}

    if trip_tracker.is_first_enable():
        seed = [
            (t.get("id") or "", t.get("endedAt") or "")
            for t in trips
            if isinstance(t, dict) and t.get("id")
        ]
        trip_tracker.seed_snapshot(seed)
        logger.info("Trip loop: first-enable snapshot of %d trip(s); no backlog will be sent", len(seed))
        return {"status": "seeded", "count": len(seed)}

    # Oldest first so the household sees trips in the order they happened.
    # endedAt is ISO-8601 so string sort matches chronological order.
    candidates = [t for t in trips if isinstance(t, dict) and t.get("id")]
    candidates.sort(key=lambda t: t.get("endedAt") or "")

    summary_queue: list[dict] = []
    task_queue: list[tuple[str, str, str]] = []  # (trip_id, ended_at, item_label)
    for trip in candidates:
        trip_id = trip["id"]
        ended_at = trip.get("endedAt") or ""
        state = trip_tracker.get(trip_id)
        # First-time trip (not in tracker) OR a trip we've partially processed
        # (summary uncertain/pending, or tasks still to create). A snapshot
        # row seeded on first enable is always summary_status='sent' with no
        # unbought, so it never re-enters either queue.
        if state is None or state.summary_status in ("pending", "uncertain"):
            summary_queue.append(trip)
        for item in (trip.get("unbought") or []):
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or item.get("requestedName") or "").strip()
            if not name:
                continue
            qty = item.get("quantity")
            label = _format_item_label(name, qty)
            key = trip_tracker.task_dedup_key(trip_id, label)
            if state is not None and key in state.tasks_created:
                continue
            task_queue.append((trip_id, ended_at, label))

    results = {"summaries_sent": 0, "tasks_created": 0, "uncertain": 0}

    for trip in summary_queue:
        trip_id = trip["id"]
        ended_at = trip.get("endedAt") or ""
        try:
            text = trip_summary.compose(trip)
            await deps.send_summary(text)
            trip_tracker.mark_summary(trip_id, "sent", ended_at)
            results["summaries_sent"] += 1
        except Exception:
            logger.exception("Trip loop: summary send failed for trip %s", trip_id)
            trip_tracker.mark_summary(trip_id, "uncertain", ended_at)
            results["uncertain"] += 1

    for trip_id, ended_at, label in task_queue:
        key = trip_tracker.task_dedup_key(trip_id, label)
        try:
            deps.add_shopping(label, _LOOP_AUTHOR)
            deps.add_tasks(label, _LOOP_AUTHOR)
            trip_tracker.mark_task_done(trip_id, key, ended_at)
            results["tasks_created"] += 1
        except Exception:
            logger.exception(
                "Trip loop: task create failed for trip %s item %r", trip_id, label
            )
            trip_tracker.mark_task_uncertain(trip_id, key, ended_at)
            results["uncertain"] += 1

    # On a brand-new trip we haven't recorded at all yet (no summary queued
    # because e.g. the loop couldn't reach the API cleanly earlier and this
    # tick is the first time we see it AND the send failed), make sure the
    # trip has a row so is_first_enable() stays False and the next tick's
    # reconcile sees it. mark_summary('pending') is a no-op idempotent write
    # when the row already exists in any state.
    for trip in candidates:
        if trip_tracker.get(trip["id"]) is None:
            trip_tracker.mark_summary(trip["id"], "pending", trip.get("endedAt") or "")

    if summary_queue or task_queue:
        logger.info(
            "Trip loop: %d summar%s sent, %d task%s created, %d uncertain",
            results["summaries_sent"], "y" if results["summaries_sent"] == 1 else "ies",
            results["tasks_created"], "" if results["tasks_created"] == 1 else "s",
            results["uncertain"],
        )
    return {"status": "ok", **results}


def _format_item_label(name: str, quantity: Any) -> str:
    try:
        qty = float(quantity or 1)
    except (TypeError, ValueError):
        qty = 1.0
    if qty == 1.0:
        return name
    if qty == int(qty):
        return f"{int(qty)} {name}"
    return f"{qty:g} {name}"
