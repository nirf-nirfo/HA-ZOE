import json
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.logging_config import logger
from app.settings import settings

# A monitor periodically checks one device's state until an end time, and alerts
# the user when the device is NOT in the expected state. Edge-triggered: it alerts
# once when the device leaves the expected state, not on every check, to avoid spam.


@dataclass
class Monitor:
    id: str
    sender: str
    entity_id: str
    entity_name: str
    expected_state: str
    alert_text: str
    interval_minutes: float
    next_check: float  # unix ts of the next check
    until: float  # unix ts to stop monitoring
    last_state: str | None = None


def _load() -> list[Monitor]:
    path = Path(settings.monitors_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Monitor(**m) for m in data]
    except Exception:
        logger.warning("Could not load monitors file, starting fresh")
        return []


def _save(monitors: list[Monitor]) -> None:
    path = Path(settings.monitors_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(m) for m in monitors], ensure_ascii=False),
        encoding="utf-8",
    )


def add_monitor(
    sender: str,
    entity_id: str,
    entity_name: str,
    expected_state: str,
    alert_text: str,
    interval_minutes: float,
    until: float,
) -> Monitor:
    monitors = _load()
    monitor = Monitor(
        id=str(uuid.uuid4())[:6],
        sender=sender,
        entity_id=entity_id,
        entity_name=entity_name,
        expected_state=expected_state,
        alert_text=alert_text,
        interval_minutes=interval_minutes,
        next_check=time.time(),  # first check on the next loop tick, to catch a current bad state
        until=until,
        last_state=None,
    )
    monitors.append(monitor)
    _save(monitors)
    return monitor


def list_monitors(sender: str) -> list[Monitor]:
    now = time.time()
    return [m for m in _load() if m.sender == sender and m.until > now]


def get_due(now: float) -> list[Monitor]:
    """Active monitors whose next check is due."""
    return [m for m in _load() if m.next_check <= now and m.until > now]


def advance(monitor_id: str, next_check: float, last_state: str) -> None:
    """Records the result of a check: schedules the next one and stores last_state."""
    monitors = _load()
    for i, m in enumerate(monitors):
        if m.id == monitor_id:
            monitors[i] = replace(m, next_check=next_check, last_state=last_state)
            _save(monitors)
            return


def purge_expired(now: float) -> int:
    """Removes monitors whose end time has passed. Returns how many were removed."""
    monitors = _load()
    remaining = [m for m in monitors if m.until > now]
    removed = len(monitors) - len(remaining)
    if removed:
        _save(remaining)
    return removed


def find_matching(sender: str, identifier: str) -> list[Monitor]:
    """Resolves a monitor from its id or a snippet of the device name it watches."""
    ident = identifier.strip()
    active = list_monitors(sender)
    by_id = [m for m in active if m.id == ident]
    if by_id:
        return by_id
    needle = ident.lower()
    return [m for m in active if needle and needle in m.entity_name.lower()]


def delete_monitor(monitor_id: str, sender: str) -> bool:
    monitors = _load()
    new = [m for m in monitors if not (m.id == monitor_id and m.sender == sender)]
    if len(new) == len(monitors):
        return False
    _save(new)
    return True
