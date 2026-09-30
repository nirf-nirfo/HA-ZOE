import time
from dataclasses import asdict, dataclass

from app._store import Store
from app.settings import settings

# Records the most recent inbound message timestamp per allowed sender, plus
# whether we've already warned them this cycle. Used to warn before Meta's 24h
# free-tier window closes so the sender can send a quick message to reopen it.


@dataclass
class InboundState:
    last_inbound_at: float
    warned_23h: bool = False


_store: Store[InboundState] = Store(lambda: settings.inbound_tracker_path, InboundState)


def _load() -> dict[str, InboundState]:
    raw = _store.load_dict()
    return {
        sender: _store._from_dict(row)
        for sender, row in raw.items()
        if isinstance(row, dict)
    }


def _save(state: dict[str, InboundState]) -> None:
    _store.save_dict({sender: asdict(row) for sender, row in state.items()})


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
