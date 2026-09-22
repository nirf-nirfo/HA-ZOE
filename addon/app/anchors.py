import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from app.logging_config import logger
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


@dataclass
class Suppression:
    anchor_id: str
    date: str  # ISO YYYY-MM-DD


def _load() -> dict:
    path = Path(settings.anchors_path)
    if not path.exists():
        return {"anchors": [], "suppressions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "anchors": [Anchor(**a) for a in data.get("anchors", [])],
            "suppressions": [Suppression(**s) for s in data.get("suppressions", [])],
        }
    except Exception:
        logger.warning("Could not load anchors file, starting fresh")
        return {"anchors": [], "suppressions": []}


def _save(data: dict) -> None:
    path = Path(settings.anchors_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "anchors": [asdict(a) for a in data["anchors"]],
                "suppressions": [asdict(s) for s in data["suppressions"]],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def add_anchor(day: str, text: str, time_hhmm: str | None) -> Anchor:
    data = _load()
    a = Anchor(
        id=str(uuid.uuid4())[:6],
        day=day.lower(),
        text=text,
        time=time_hhmm,
        added_at=time.time(),
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


def purge_suppressions_before(cutoff_date: str) -> int:
    data = _load()
    new_supps = [s for s in data["suppressions"] if s.date >= cutoff_date]
    removed = len(data["suppressions"]) - len(new_supps)
    if removed:
        _save({"anchors": data["anchors"], "suppressions": new_supps})
    return removed
