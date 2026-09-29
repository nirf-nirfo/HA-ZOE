import time
import uuid
from dataclasses import asdict, dataclass

from app._store import Store
from app.settings import settings


@dataclass
class ListItem:
    id: str
    text: str
    added_by: str
    added_at: float


_store: Store[ListItem] = Store(lambda: settings.lists_path, ListItem)


def _load() -> dict[str, list[ListItem]]:
    # Sub-item filtering happens here: Store.load_dict returns the raw dict,
    # then each row goes through the Store's per-instance _from_dict for
    # unknown-key tolerance.
    raw = _store.load_dict()
    return {
        name: [_store._from_dict(i) for i in items if isinstance(i, dict)]
        for name, items in raw.items()
        if isinstance(items, list)
    }


def _save(data: dict[str, list[ListItem]]) -> None:
    _store.save_dict(
        {name: [asdict(i) for i in items] for name, items in data.items()}
    )


def add_item(list_name: str, text: str, sender: str) -> ListItem:
    data = _load()
    item = ListItem(id=str(uuid.uuid4())[:6], text=text, added_by=sender, added_at=time.time())
    data.setdefault(list_name, []).append(item)
    _save(data)
    return item


def remove_items(list_name: str, text: str) -> list[str]:
    """Removes all items whose text contains `text` (case-insensitive). Returns removed texts."""
    data = _load()
    items = data.get(list_name, [])
    needle = text.strip().lower()
    kept, removed = [], []
    for item in items:
        if needle in item.text.lower():
            removed.append(item.text)
        else:
            kept.append(item)
    if removed:
        data[list_name] = kept
        _save(data)
    return removed


def clear_list(list_name: str) -> int:
    data = _load()
    count = len(data.get(list_name, []))
    data[list_name] = []
    _save(data)
    return count


def get_list(list_name: str) -> list[ListItem]:
    return _load().get(list_name, [])


def get_all_list_names() -> list[tuple[str, int]]:
    """Returns (name, item_count) for every non-empty list, sorted by name."""
    data = _load()
    return sorted((name, len(items)) for name, items in data.items() if items)
