import time

# Short-term per-sender conversation memory, so follow-ups like "turn it on" or
# "cancel it" resolve against what was just discussed. In-process and ephemeral by
# design; cleared after a period of silence so stale context can't leak into a new
# topic (and so a restart simply starts the thread fresh).

_history: dict[str, list[dict]] = {}
_last_seen: dict[str, float] = {}

_MAX_MESSAGES = 10  # keep roughly the last 5 exchanges
_TTL_SECONDS = 30 * 60  # forget the thread after 30 minutes idle


def recent(sender: str) -> list[dict]:
    """Prior user/assistant turns for this sender, or [] if the thread went idle."""
    if time.time() - _last_seen.get(sender, 0.0) > _TTL_SECONDS:
        _history.pop(sender, None)
        _last_seen.pop(sender, None)
    return list(_history.get(sender, []))


def record(sender: str, user_text: str, assistant_text: str) -> None:
    """Appends one exchange to the sender's short-term history, trimmed to the window."""
    turns = _history.get(sender, [])
    turns.append({"role": "user", "content": user_text})
    turns.append({"role": "assistant", "content": assistant_text})
    _history[sender] = turns[-_MAX_MESSAGES:]
    _last_seen[sender] = time.time()
