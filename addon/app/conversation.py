import time

from app._store import Store
from app.settings import settings

# Short-term per-sender conversation memory: prior user/assistant turns so
# follow-ups like "turn it on" or "cancel it" resolve against what was just
# discussed. Persisted so an add-on restart doesn't drop the thread mid-way.
# Held to a 24h idle window and a rolling exchange cap — anything older is
# available only through the searchable long-term log (conversation_log.py).

_MAX_MESSAGES = 40  # 20 exchanges
_TTL_SECONDS = 24 * 60 * 60

# Assistant-turn phrases that mark a bad refusal — Zoe shouldn't have said
# this, and letting the LLM see its own prior refusal in the replayed history
# creates a few-shot bias that re-emits the same refusal on the next identical
# input, even after a SYSTEM_PROMPT change forbids it. We strip the whole
# exchange (the user turn that preceded it too) so the thread reads as if the
# refusal never happened. Keep this list narrow — stripping too aggressively
# would lose real context — and only add phrases that are unambiguously
# refusal shapes the system prompt now bans.
_BAD_ASSISTANT_PHRASES = (
    "לא הצלחתי להבין",
    "נסה שוב — למשל",
)


# Raw dict payload — no dataclass, just needs corruption-tolerant load and
# atomic write.
_store: Store = Store(lambda: settings.conversation_path)


def _load() -> dict:
    return _store.load_dict()


def _save(data: dict) -> None:
    _store.save_dict(data)


def _is_bad_refusal(turn: dict) -> bool:
    if turn.get("role") != "assistant":
        return False
    content = turn.get("content")
    if not isinstance(content, str):
        return False
    return any(p in content for p in _BAD_ASSISTANT_PHRASES)


def _strip_bad_refusals(turns: list[dict]) -> list[dict]:
    """Drops assistant refusals matching _BAD_ASSISTANT_PHRASES along with the
    user turn immediately preceding each one. Preserves order of everything else."""
    out: list[dict] = []
    for turn in turns:
        if _is_bad_refusal(turn):
            if out and out[-1].get("role") == "user":
                out.pop()
            continue
        out.append(turn)
    return out


def recent(sender: str) -> list[dict]:
    """Prior user/assistant turns for this sender, or [] if the thread went idle
    past the TTL. Returned as chat-formatted messages ready to prepend to a run."""
    data = _load()
    entry = data.get(sender)
    if not entry:
        return []
    if time.time() - entry.get("last_seen", 0.0) > _TTL_SECONDS:
        return []
    return _strip_bad_refusals(list(entry.get("turns", [])))


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
