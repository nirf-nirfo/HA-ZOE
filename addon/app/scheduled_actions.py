import time
import uuid
from dataclasses import dataclass

from app._store import Store
from app.settings import settings

# A scheduled action is a device command Zoe executes for real at a future time —
# distinct from a reminder (which only sends a text message). One-shot: it runs once
# and is removed. Risky devices are not auto-executed; they're queued for the normal
# yes/confirm flow at run time instead.


@dataclass
class ScheduledAction:
    id: str
    sender: str
    entity_id: str
    entity_name: str
    domain: str
    service: str
    service_data: dict
    description: str
    run_at: float  # unix ts
    duration_minutes: float | None = None


_store: Store[ScheduledAction] = Store(lambda: settings.scheduled_actions_path, ScheduledAction)


def _load() -> list[ScheduledAction]:
    return _store.load_list()


def _save(actions: list[ScheduledAction]) -> None:
    _store.save_list(actions)


def find_duplicate(
    sender: str, entity_id: str, service: str, service_data: dict, run_at: float
) -> ScheduledAction | None:
    """Guards against Meta redelivering the same request as a fresh message (see
    reminders.find_duplicate) producing two identical scheduled actions."""
    for a in _load():
        if (
            a.sender == sender
            and a.entity_id == entity_id
            and a.service == service
            and a.service_data == service_data
            and a.run_at == run_at
        ):
            return a
    return None


def add_action(
    sender: str,
    entity_id: str,
    entity_name: str,
    domain: str,
    service: str,
    service_data: dict,
    description: str,
    run_at: float,
    duration_minutes: float | None = None,
) -> ScheduledAction:
    actions = _load()
    action = ScheduledAction(
        id=str(uuid.uuid4())[:6],
        sender=sender,
        entity_id=entity_id,
        entity_name=entity_name,
        domain=domain,
        service=service,
        service_data=service_data,
        description=description,
        run_at=run_at,
        duration_minutes=duration_minutes,
    )
    actions.append(action)
    _save(actions)
    return action


def list_actions(sender: str) -> list[ScheduledAction]:
    now = time.time()
    items = [a for a in _load() if a.sender == sender and a.run_at > now]
    return sorted(items, key=lambda a: a.run_at)


def find_matching(sender: str, identifier: str) -> list[ScheduledAction]:
    """Resolves a scheduled action from its id or a snippet of the device name / description."""
    ident = identifier.strip()
    pending = list_actions(sender)
    by_id = [a for a in pending if a.id == ident]
    if by_id:
        return by_id
    needle = ident.lower()
    return [
        a
        for a in pending
        if needle and (needle in a.entity_name.lower() or needle in a.description.lower())
    ]


def delete_action(action_id: str, sender: str) -> bool:
    actions = _load()
    new = [a for a in actions if not (a.id == action_id and a.sender == sender)]
    if len(new) == len(actions):
        return False
    _save(new)
    return True


def count_pending() -> int:
    """Number of not-yet-run scheduled actions (all senders). Summary for /admin/status."""
    now = time.time()
    return sum(1 for a in _load() if a.run_at > now)


def next_fire_at() -> float | None:
    """Earliest future run_at across all senders, or None if nothing pending."""
    now = time.time()
    times = [a.run_at for a in _load() if a.run_at > now]
    return min(times) if times else None


def pop_due() -> list[ScheduledAction]:
    """Removes and returns actions whose time has come. One-shot: never rescheduled."""
    now = time.time()
    actions = _load()
    due = [a for a in actions if a.run_at <= now]
    if due:
        _save([a for a in actions if a.run_at > now])
    return due
