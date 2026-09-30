import time
import uuid
from dataclasses import asdict, dataclass, field

from app._store import Store
from app.settings import settings

# Weekly recurring "anchors": household-wide schedule items keyed by day-of-week
# (e.g. "on Sundays Mili finishes school at 13:00"). Distinct from agenda items
# (specific calendar day) and reminders (fire a message at an exact time).
# Suppressions cancel a specific anchor on a specific date without deleting the
# weekly template — for "no soccer this Sunday" type overrides.

DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


@dataclass
class Anchor:
    id: str
    day: str  # one of DAYS
    text: str
    time: str | None  # "HH:MM" (24-hour, Israel time) or None
    added_at: float
    # Free-form category tags (e.g. ["school"]). Enables briefing-time filters
    # like "suppress school anchors on days the school is closed" without the
    # anchor's free-text needing to be re-parsed. Empty on legacy rows.
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Belt-and-suspenders: a persisted `null` for tags (from a hand-edited
        # file or an older writer) would otherwise slip through Store's
        # unknown-key filter as-is and break `"school" in a.tags` later.
        if self.tags is None:
            self.tags = []


@dataclass
class Suppression:
    anchor_id: str
    date: str  # ISO YYYY-MM-DD


# Two dataclasses share one file: instantiate two Stores against the same path
# so each gets its own _from_dict for unknown-key tolerance. The Suppression
# store's I/O is unused (only its _from_dict is); _anchor_store owns the file.
_anchor_store: Store[Anchor] = Store(lambda: settings.anchors_path, Anchor)
_suppression_store: Store[Suppression] = Store(lambda: settings.anchors_path, Suppression)
_anchor_from_dict = _anchor_store._from_dict
_suppression_from_dict = _suppression_store._from_dict


def _load() -> dict:
    raw = _anchor_store.load_dict()
    return {
        "anchors": [
            _anchor_store._from_dict(a)
            for a in raw.get("anchors", []) if isinstance(a, dict)
        ],
        "suppressions": [
            _suppression_store._from_dict(s)
            for s in raw.get("suppressions", []) if isinstance(s, dict)
        ],
    }


def _save(data: dict) -> None:
    _anchor_store.save_dict(
        {
            "anchors": [asdict(a) for a in data["anchors"]],
            "suppressions": [asdict(s) for s in data["suppressions"]],
        }
    )


def add_anchor(
    day: str,
    text: str,
    time_hhmm: str | None,
    tags: list[str] | None = None,
) -> Anchor:
    data = _load()
    a = Anchor(
        id=str(uuid.uuid4())[:6],
        day=day.lower(),
        text=text,
        time=time_hhmm,
        added_at=time.time(),
        tags=list(tags) if tags else [],
    )
    data["anchors"].append(a)
    _save(data)
    return a


def list_all() -> list[Anchor]:
    return _load()["anchors"]


def find_matching(identifier: str) -> list[Anchor]:
    ident = identifier.strip()
    anchors = _load()["anchors"]
    by_id = [a for a in anchors if a.id == ident]
    if by_id:
        return by_id
    needle = ident.lower()
    return [a for a in anchors if needle and needle in a.text.lower()]


def remove_anchor(anchor_id: str) -> bool:
    data = _load()
    new_anchors = [a for a in data["anchors"] if a.id != anchor_id]
    new_supps = [s for s in data["suppressions"] if s.anchor_id != anchor_id]
    if len(new_anchors) == len(data["anchors"]):
        return False
    _save({"anchors": new_anchors, "suppressions": new_supps})
    return True


def suppress_for_date(anchor_id: str, date: str) -> bool:
    data = _load()
    if not any(a.id == anchor_id for a in data["anchors"]):
        return False
    for s in data["suppressions"]:
        if s.anchor_id == anchor_id and s.date == date:
            return True
    data["suppressions"].append(Suppression(anchor_id=anchor_id, date=date))
    _save(data)
    return True


def anchors_for_date(day: str, date: str) -> list[Anchor]:
    """Anchors for the given day-of-week, excluding ones suppressed for `date`."""
    data = _load()
    supp_ids = {s.anchor_id for s in data["suppressions"] if s.date == date}
    return [a for a in data["anchors"] if a.day == day.lower() and a.id not in supp_ids]


# Heuristic markers for a school-related anchor. Anything Hebrew or English
# that unambiguously points at "kid finishes school / school pickup / class"
# without dragging in unrelated חוגים. Kept narrow on purpose — the tool
# schema is the primary path; this is a one-time back-fill.
_SCHOOL_MARKERS = ("בית ספר", "מסיימת", "כיתה", "school")


def auto_tag_school() -> int:
    """One-shot migration: add the 'school' tag to anchors whose free text
    is clearly school-related. Idempotent — anchors already tagged 'school'
    are left alone. Returns how many rows were updated."""
    data = _load()
    updated = 0
    for a in data["anchors"]:
        if "school" in a.tags:
            continue
        text_l = (a.text or "").lower()
        # Hebrew comparison stays case-sensitive (no case in Hebrew); English
        # goes through the lowercased needle.
        if any(m in a.text for m in _SCHOOL_MARKERS if not m.isascii()) or \
           any(m in text_l for m in _SCHOOL_MARKERS if m.isascii()):
            a.tags = list(a.tags) + ["school"]
            updated += 1
    if updated:
        _save(data)
    return updated


def purge_suppressions_before(cutoff_date: str) -> int:
    data = _load()
    new_supps = [s for s in data["suppressions"] if s.date >= cutoff_date]
    removed = len(data["suppressions"]) - len(new_supps)
    if removed:
        _save({"anchors": data["anchors"], "suppressions": new_supps})
    return removed
