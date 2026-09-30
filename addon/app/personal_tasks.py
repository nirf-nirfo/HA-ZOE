import time
import uuid
from dataclasses import asdict, dataclass

from app._store import Store
from app.settings import settings

# Per-sender private task list — parallel to the household `tasks` list but
# scoped to one WhatsApp number. Each sender only ever sees or touches their
# own bucket; there is no cross-sender read path. Used for personal things
# (open a bug, email the boss) that don't belong on the family list.


@dataclass
class PersonalTask:
    id: str
    text: str
    created_at: float


_store: Store[PersonalTask] = Store(lambda: settings.personal_tasks_path, PersonalTask)
_from_dict = _store._from_dict


def _load() -> dict[str, list[dict]]:
    return _store.load_dict()


def _save(data: dict[str, list[dict]]) -> None:
    _store.save_dict(data)


def _to_tasks(raw: list[dict]) -> list[PersonalTask]:
    # Sub-item filtering has to happen here — Store.load_dict returns the raw
    # dict without touching its values, so rows written by a future version
    # get filtered through the Store's per-instance _from_dict here.
    out = []
    for r in raw:
        try:
            out.append(_store._from_dict(r))
        except Exception:
            continue
    return out


def add(sender: str, text: str) -> PersonalTask:
    data = _load()
    bucket = data.get(sender, [])
    task = PersonalTask(id=str(uuid.uuid4())[:6], text=text, created_at=time.time())
    bucket.append(asdict(task))
    data[sender] = bucket
    _save(data)
    return task


def list_for(sender: str) -> list[PersonalTask]:
    return _to_tasks(_load().get(sender, []))


def find_matching(sender: str, identifier: str) -> list[PersonalTask]:
    ident = identifier.strip()
    tasks = list_for(sender)
    by_id = [t for t in tasks if t.id == ident]
    if by_id:
        return by_id
    needle = ident.lower()
    return [t for t in tasks if needle and needle in t.text.lower()]


def complete(sender: str, task_id: str) -> PersonalTask | None:
    data = _load()
    bucket = data.get(sender, [])
    for i, raw in enumerate(bucket):
        if raw.get("id") == task_id:
            removed = bucket.pop(i)
            data[sender] = bucket
            _save(data)
            try:
                return _store._from_dict(removed)
            except Exception:
                return None
    return None


def count_by_sender() -> dict[str, int]:
    """{sender_phone: open_task_count}. Summary for /admin/status."""
    return {sender: len(bucket) for sender, bucket in _load().items()}


def clear(sender: str) -> int:
    data = _load()
    bucket = data.get(sender, [])
    count = len(bucket)
    if count:
        data[sender] = []
        _save(data)
    return count
