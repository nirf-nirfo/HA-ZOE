import json
import time
from pathlib import Path

from app.logging_config import logger
from app.settings import settings

# Persistent append-only log of every exchange, searchable long after the
# short-term conversation window (conversation.py) has expired. Capped at 90
# days so the file can't grow forever. Reachable through the
# search_past_conversations tool — ZOE queries it when the user references
# something discussed before that isn't in the current thread anymore.

_MAX_AGE_SECONDS = 90 * 24 * 60 * 60
_MAX_RESULTS = 15


def _load() -> list[dict]:
    path = Path(settings.conversation_log_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        logger.warning("Could not load conversation log file, starting fresh")
        return []


def _save(entries: list[dict]) -> None:
    path = Path(settings.conversation_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def append(sender: str, user_text: str, assistant_text: str) -> None:
    """Records one exchange, pruning anything older than the retention window."""
    entries = _load()
    cutoff = time.time() - _MAX_AGE_SECONDS
    entries = [e for e in entries if e.get("ts", 0) > cutoff]
    entries.append(
        {
            "sender": sender,
            "ts": time.time(),
            "user": user_text,
            "assistant": assistant_text,
        }
    )
    _save(entries)


def search(sender: str, query: str, days_back: int | None = None) -> list[dict]:
    """Case-insensitive substring search over this sender's own history, newest
    first, capped to a handful of results so a search can't blow up the model's
    context window."""
    needle = query.strip().lower()
    if not needle:
        return []
    entries = _load()
    if days_back is not None and days_back > 0:
        cutoff = time.time() - days_back * 24 * 60 * 60
        entries = [e for e in entries if e.get("ts", 0) > cutoff]
    matches = [
        e
        for e in entries
        if e.get("sender") == sender
        and (
            needle in (e.get("user") or "").lower()
            or needle in (e.get("assistant") or "").lower()
        )
    ]
    matches.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return matches[:_MAX_RESULTS]
