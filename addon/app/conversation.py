import json
import time
from pathlib import Path

from app.logging_config import logger
from app.settings import settings

# Short-term per-sender conversation memory: prior user/assistant turns so
# follow-ups like "turn it on" or "cancel it" resolve against what was just
# discussed. Persisted so an add-on restart doesn't drop the thread mid-way.
# Held to a 24h idle window and a rolling exchange cap — anything older is
# available only through the searchable long-term log (conversation_log.py).

_MAX_MESSAGES = 40  # 20 exchanges
_TTL_SECONDS = 24 * 60 * 60


def _load() -> dict:
    path = Path(settings.conversation_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not load conversation file, starting fresh")
        return {}


def _save(data: dict) -> None:
    path = Path(settings.conversation_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def recent(sender: str) -> list[dict]:
    """Prior user/assistant turns for this sender, or [] if the thread went idle
    past the TTL. Returned as chat-formatted messages ready to prepend to a run."""
    data = _load()
    entry = data.get(sender)
    if not entry:
        return []
    if time.time() - entry.get("last_seen", 0.0) > _TTL_SECONDS:
        return []
    return list(entry.get("turns", []))


def record(sender: str, user_text: str, assistant_text: str) -> None:
    """Appends one exchange to the sender's rolling short-term history."""
    data = _load()
    entry = data.get(sender) or {"turns": [], "last_seen": 0.0}
    turns = entry["turns"]
    turns.append({"role": "user", "content": user_text})
    turns.append({"role": "assistant", "content": assistant_text})
    entry["turns"] = turns[-_MAX_MESSAGES:]
    entry["last_seen"] = time.time()
    data[sender] = entry
    _save(data)
