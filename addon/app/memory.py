import time
import uuid
from dataclasses import dataclass

from app._store import Store
from app.settings import settings

# ZOE's long-term memory: durable facts about the user and household that should
# persist across conversations (preferences, names, defaults). Shared for the whole
# family, like lists — not keyed per sender.


@dataclass
class Fact:
    id: str
    text: str
    added_at: float


_store: Store[Fact] = Store(lambda: settings.memory_path, Fact)
_from_dict = _store._from_dict


def _load() -> list[Fact]:
    return _store.load_list()


def _save(facts: list[Fact]) -> None:
    _store.save_list(facts)


def remember(text: str) -> Fact | None:
    """Saves a fact. Returns None (without duplicating) if an identical fact exists."""
    text = text.strip()
    facts = _load()
    if any(f.text.strip().lower() == text.lower() for f in facts):
        return None
    fact = Fact(id=str(uuid.uuid4())[:6], text=text, added_at=time.time())
    facts.append(fact)
    _save(facts)
    return fact


def forget(text: str) -> list[str]:
    """Removes all facts whose text contains `text` (case-insensitive). Returns removed texts."""
    needle = text.strip().lower()
    facts = _load()
    kept, removed = [], []
    for f in facts:
        if needle and needle in f.text.lower():
            removed.append(f.text)
        else:
            kept.append(f)
    if removed:
        _save(kept)
    return removed


def all_facts() -> list[Fact]:
    return _load()
