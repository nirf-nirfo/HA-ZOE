import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from app.logging_config import logger
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


def _load() -> dict[str, list[dict]]:
    path = Path(settings.personal_tasks_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("Could not load personal_tasks file, starting fresh")
        return {}


def _save(data: dict[str, list[dict]]) -> None:
    path = Path(settings.personal_tasks_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _to_tasks(raw: list[dict]) -> list[PersonalTask]:
    out = []
    for r in raw:
        try:
            out.append(PersonalTask(**r))
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
                return PersonalTask(**removed)
            except Exception:
                return None
    return None


def clear(sender: str) -> int:
    data = _load()
    bucket = data.get(sender, [])
    count = len(bucket)
    if count:
        data[sender] = []
        _save(data)
    return count
