import json
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app.logging_config import logger
from app.settings import settings

# Records the most recent inbound message timestamp per allowed sender, plus
# whether we've already warned them this cycle. Used to warn before Meta's 24h
# free-tier window closes so the sender can send a quick message to reopen it.


@dataclass
class InboundState:
    last_inbound_at: float
    warned_23h: bool = False


_FIELDS = {f.name for f in fields(InboundState)}


def _from_dict(d: dict) -> InboundState:
    # Drop unknown keys so a persisted row from a future version (or a manual edit)
    # cannot poison the whole store with a TypeError.
    return InboundState(**{k: v for k, v in d.items() if k in _FIELDS})


def _load() -> dict[str, InboundState]:
    path = Path(settings.inbound_tracker_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {sender: _from_dict(row) for sender, row in data.items() if isinstance(row, dict)}
    except Exception:
        logger.warning("Could not load inbound_tracker file, starting fresh")
        return {}


def _save(state: dict[str, InboundState]) -> None:
    path = Path(settings.inbound_tracker_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({sender: asdict(row) for sender, row in state.items()}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def record_inbound(sender: str) -> None:
    """Called on every allowed inbound message: refreshes the timestamp and
    clears the warned flag so the 23h warning can fire again next cycle."""
    state = _load()
    state[sender] = InboundState(last_inbound_at=time.time(), warned_23h=False)
    _save(state)


def all_senders() -> dict[str, InboundState]:
    return _load()


def mark_warned(sender: str) -> None:
    state = _load()
    entry = state.get(sender)
    if entry is None:
        return
    entry.warned_23h = True
    _save(state)
