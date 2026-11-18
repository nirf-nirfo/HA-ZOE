"""Post-shop trip tracking for the grocery-app integration.

Persists per-trip effect progress so the `_grocery_trip_loop` can be
restarted, crash mid-run, or miss a window without duplicating sends or
household tasks. One row per completed trip:

    TripState(
        trip_id="...",             # opaque; from the grocery API
        ended_at="ISO-8601",       # for ordering only, not for recency compare
        summary_status="sent" | "uncertain" | "pending",
        tasks_created=["key1", ...],   # stable dedup keys that landed
        tasks_uncertain=["keyN"],      # creates whose outcome is unknown
        snapshot=False,                # True → seeded on first enable, no
                                       # effects should ever run for it
    )

The dedup key for a task is sha256("<trip_id>|<normalized name>")[:40]; the
same name on the same trip is never created twice, and a restart after a
partial run of tasks resumes from `tasks_created`.

Both totals (paidAgorot / estimateAgorot) and bought/unbought lines are
NOT stored — they're derived from the grocery-API payload at effect-apply
time. Storing them here would only invite drift between the source of
truth and our local copy for a benefit nobody asked for.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Iterable

from app._store import Store
from app.settings import settings


@dataclass
class TripState:
    trip_id: str
    ended_at: str = ""
    summary_status: str = "pending"  # pending | sent | uncertain
    tasks_created: list[str] = field(default_factory=list)
    tasks_uncertain: list[str] = field(default_factory=list)
    snapshot: bool = False


_store: Store[TripState] = Store(lambda: settings.trip_tracker_path, TripState)


def _load() -> list[TripState]:
    rows = _store.load_list()
    # Belt-and-suspenders: _store.load_list already drops unknown keys, but
    # defensively coerce the list fields in case a corrupt payload replaced
    # them with a non-list.
    out: list[TripState] = []
    for r in rows:
        tasks_created = list(r.tasks_created) if isinstance(r.tasks_created, list) else []
        tasks_uncertain = list(r.tasks_uncertain) if isinstance(r.tasks_uncertain, list) else []
        out.append(replace(r, tasks_created=tasks_created, tasks_uncertain=tasks_uncertain))
    return out


def _save(rows: list[TripState]) -> None:
    _store.save_list(rows)


def _index(rows: list[TripState]) -> dict[str, TripState]:
    return {r.trip_id: r for r in rows if r.trip_id}


def _norm_name(name: str) -> str:
    """Folds whitespace so "חלב " and "חלב" dedup to the same task key."""
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def task_dedup_key(trip_id: str, name: str) -> str:
    """Stable, short dedup key for 'this item from this trip'. Trip id +
    normalized name, sha256 → 40 hex chars. Collisions are astronomically
    unlikely for the household's scale and the characters are safe to log."""
    raw = f"{trip_id}|{_norm_name(name)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


# -------- queries ----------------------------------------------------------


def known_trip_ids() -> set[str]:
    return {r.trip_id for r in _load() if r.trip_id}


def get(trip_id: str) -> TripState | None:
    return _index(_load()).get(trip_id)


def is_first_enable() -> bool:
    """True when the tracker is empty — the loop is seeing the household's
    history for the first time and should snapshot it rather than treat
    every past trip as a backlog to send."""
    return not _load()


# -------- mutations --------------------------------------------------------


def seed_snapshot(trip_ids: Iterable[tuple[str, str]]) -> None:
    """First-enable seed: mark every supplied trip as already-processed so
    no backlog of historical summaries or tasks ever gets sent. Each tuple
    is (trip_id, ended_at). Idempotent: re-seeding is a no-op when the
    tracker is non-empty."""
    rows = _load()
    if rows:
        return
    seeded = [
        TripState(
            trip_id=trip_id,
            ended_at=ended_at or "",
            summary_status="sent",
            snapshot=True,
        )
        for trip_id, ended_at in trip_ids
        if trip_id
    ]
    _save(seeded)


def upsert(state: TripState) -> TripState:
    rows = _load()
    idx = _index(rows)
    if state.trip_id in idx:
        rows = [state if r.trip_id == state.trip_id else r for r in rows]
    else:
        rows.append(state)
    _save(rows)
    return state


def mark_summary(trip_id: str, status: str, ended_at: str = "") -> TripState:
    rows = _load()
    idx = _index(rows)
    row = idx.get(trip_id) or TripState(trip_id=trip_id, ended_at=ended_at)
    row = replace(row, summary_status=status, ended_at=row.ended_at or ended_at)
    return upsert(row)


def mark_task_done(trip_id: str, key: str, ended_at: str = "") -> TripState:
    rows = _load()
    idx = _index(rows)
    row = idx.get(trip_id) or TripState(trip_id=trip_id, ended_at=ended_at)
    created = list(row.tasks_created)
    uncertain = [k for k in row.tasks_uncertain if k != key]
    if key not in created:
        created.append(key)
    row = replace(
        row,
        tasks_created=created,
        tasks_uncertain=uncertain,
        ended_at=row.ended_at or ended_at,
    )
    return upsert(row)


def mark_task_uncertain(trip_id: str, key: str, ended_at: str = "") -> TripState:
    rows = _load()
    idx = _index(rows)
    row = idx.get(trip_id) or TripState(trip_id=trip_id, ended_at=ended_at)
    if key in row.tasks_created:
        return row
    uncertain = list(row.tasks_uncertain)
    if key not in uncertain:
        uncertain.append(key)
    row = replace(
        row,
        tasks_uncertain=uncertain,
        ended_at=row.ended_at or ended_at,
    )
    return upsert(row)


def _reset_for_tests() -> None:
    """Test helper: clears the on-disk tracker."""
    _save([])
