"""Item 21: declarative inbox watches.

A watch is a saved filter the email watch loop evaluates every tick. On each
matching message it hasn't already processed, the loop hands the full fetched
message off to `email_processor.process_email` (Item 20), which decides what
to extract (receipt → expense, iCal → agenda, ...). Deduplication is handled
here by remembering the last 100 UIDs each watch has acted on.

Watches are household-wide — email isn't attributable to one phone, and the
Item 20 processor already broadcasts the outcome of a successful extraction.
That matches the existing household boundary on `expenses` and keeps a watch
set up by one spouse from being invisible to the other.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime

from app._store import Store
from app.settings import settings

# Rolling cap on `last_processed_uids`. Keeps the per-watch JSON row small
# (watches persist across restarts) while still being long enough that normal
# IMAP UID churn — new messages, server-side renumbering on folder changes —
# can't push a yet-to-be-processed UID back into the "unseen" set within one
# day's traffic.
_MAX_UIDS = 100


@dataclass
class EmailWatch:
    id: str
    name: str
    from_contains: str = ""
    subject_contains: str = ""
    body_contains: str = ""
    interval_minutes: int = 60
    last_checked_at: datetime | None = None
    last_processed_uids: list[str] = field(default_factory=list)
    enabled: bool = True


_store: Store[EmailWatch] = Store(lambda: settings.email_watches_path, EmailWatch)
# Re-exported for parity with the sibling stores — tests that need to coerce a
# raw dict row (e.g. legacy-row load) use the same hook.
_from_dict = _store._from_dict


def _load() -> list[EmailWatch]:
    rows = _store.load_list()
    # `last_checked_at` serialises to ISO string via asdict → json; dataclasses
    # don't rehydrate it back to a datetime automatically. Normalise here so
    # the loop can compare timestamps without re-parsing on every tick.
    fixed: list[EmailWatch] = []
    for r in rows:
        ts = r.last_checked_at
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                ts = None
            r = replace(r, last_checked_at=ts)
        fixed.append(r)
    return fixed


def _save(items: list[EmailWatch]) -> None:
    # Serialise datetime → ISO string so Store's asdict + json.dumps round-trip
    # safely. We keep the dataclass's `datetime | None` field for ergonomic
    # Python-side comparisons.
    normalised: list[EmailWatch] = []
    for w in items:
        if isinstance(w.last_checked_at, datetime):
            w = replace(w, last_checked_at=w.last_checked_at.isoformat())  # type: ignore[arg-type]
        normalised.append(w)
    _store.save_list(normalised)


def add(
    name: str,
    from_contains: str = "",
    subject_contains: str = "",
    body_contains: str = "",
    interval_minutes: int = 60,
) -> EmailWatch:
    """Create a new watch. At least one of the three filter fields should be set
    in practice — the tool handler enforces that; the store itself stays permissive
    so a future migration or import step can round-trip blank filters."""
    watches = _load()
    w = EmailWatch(
        id=str(uuid.uuid4())[:6],
        name=name,
        from_contains=from_contains,
        subject_contains=subject_contains,
        body_contains=body_contains,
        interval_minutes=max(int(interval_minutes), 1),
    )
    watches.append(w)
    _save(watches)
    return w


def list_watches() -> list[EmailWatch]:
    return _load()


def delete(watch_id: str) -> bool:
    watches = _load()
    remaining = [w for w in watches if w.id != watch_id]
    if len(remaining) == len(watches):
        return False
    _save(remaining)
    return True


def _set_enabled(watch_id: str, enabled: bool) -> bool:
    watches = _load()
    changed = False
    out: list[EmailWatch] = []
    for w in watches:
        if w.id == watch_id:
            out.append(replace(w, enabled=enabled))
            changed = True
        else:
            out.append(w)
    if changed:
        _save(out)
    return changed


def enable(watch_id: str) -> bool:
    return _set_enabled(watch_id, True)


def disable(watch_id: str) -> bool:
    return _set_enabled(watch_id, False)


def update_last_checked(watch_id: str, when: datetime) -> None:
    """Records that the loop just finished checking this watch at `when`. Any
    unknown id is an idempotent no-op — a watch may have been deleted mid-tick."""
    watches = _load()
    changed = False
    out: list[EmailWatch] = []
    for w in watches:
        if w.id == watch_id:
            out.append(replace(w, last_checked_at=when))
            changed = True
        else:
            out.append(w)
    if changed:
        _save(out)


def mark_uids_processed(watch_id: str, uids: list[str]) -> None:
    """Appends `uids` to the watch's processed list, deduped, with oldest-first
    eviction once the list exceeds _MAX_UIDS. No-op when uids is empty or the
    watch is gone."""
    if not uids:
        return
    watches = _load()
    changed = False
    out: list[EmailWatch] = []
    for w in watches:
        if w.id == watch_id:
            seen = set(w.last_processed_uids)
            merged = list(w.last_processed_uids)
            for u in uids:
                if u not in seen:
                    merged.append(u)
                    seen.add(u)
            # Oldest-first eviction keeps the newest _MAX_UIDS entries — the
            # tail of the list, since appends go to the end.
            if len(merged) > _MAX_UIDS:
                merged = merged[-_MAX_UIDS:]
            out.append(replace(w, last_processed_uids=merged))
            changed = True
        else:
            out.append(w)
    if changed:
        _save(out)


def get(watch_id: str) -> EmailWatch | None:
    for w in _load():
        if w.id == watch_id:
            return w
    return None


def count_all() -> int:
    """Total number of stored watches. Summary for /admin/status."""
    return len(_load())


def count_enabled() -> int:
    """Watches currently enabled. Summary for /admin/status."""
    return sum(1 for w in _load() if w.enabled)
