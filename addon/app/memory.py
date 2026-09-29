import json
import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app.logging_config import logger
from app.settings import settings

# ZOE's long-term memory: durable facts about the user and household that should
# persist across conversations (preferences, names, defaults). Shared for the whole
# family, like lists — not keyed per sender.


@dataclass
class Fact:
    id: str
    text: str
    added_at: float


_FIELDS = {f.name for f in fields(Fact)}


def _from_dict(d: dict) -> Fact:
    # Drop unknown keys so a row from a future version can't kill the load.
    return Fact(**{k: v for k, v in d.items() if k in _FIELDS})


def _load() -> list[Fact]:
    path = Path(settings.memory_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [_from_dict(f) for f in data]
    except Exception:
        logger.warning("Could not load memory file, starting fresh")
        return []


def _save(facts: list[Fact]) -> None:
    path = Path(settings.memory_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(f) for f in facts], ensure_ascii=False),
        encoding="utf-8",
    )


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
