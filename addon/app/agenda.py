import time
import uuid
from dataclasses import dataclass

from app._store import Store
from app.settings import settings

# Agenda items are fed in advance for a specific calendar day (not a time-of-day),
# and read out in the daily morning briefing for that day. Separate from reminders
# (which fire messages at an exact time) and monitors (which watch device state).


@dataclass
class AgendaItem:
    id: str
    sender: str
    date: str  # ISO date, YYYY-MM-DD
    text: str
    added_at: float


_store: Store[AgendaItem] = Store(lambda: settings.agenda_path, AgendaItem)


def _load() -> list[AgendaItem]:
    return _store.load_list()


def _save(items: list[AgendaItem]) -> None:
    _store.save_list(items)


def add_item(sender: str, date: str, text: str) -> AgendaItem:
    items = _load()
    item = AgendaItem(id=str(uuid.uuid4())[:6], sender=sender, date=date, text=text, added_at=time.time())
    items.append(item)
    _save(items)
    return item


def items_for_date(sender: str, date: str) -> list[AgendaItem]:
    return [i for i in _load() if i.sender == sender and i.date == date]


def remove_item(sender: str, date: str, text_snippet: str) -> list[str]:
    """Removes items on `date` whose text contains `text_snippet` (case-insensitive). Returns removed texts."""
    needle = text_snippet.strip().lower()
    items = _load()
    kept, removed = [], []
    for i in items:
        if i.sender == sender and i.date == date and needle and needle in i.text.lower():
            removed.append(i.text)
        else:
            kept.append(i)
    if removed:
        _save(kept)
    return removed


def purge_before(cutoff_date: str) -> int:
    """Removes agenda items dated before cutoff_date (ISO). Returns how many were removed."""
    items = _load()
    remaining = [i for i in items if i.date >= cutoff_date]
    removed = len(items) - len(remaining)
    if removed:
        _save(remaining)
    return removed
