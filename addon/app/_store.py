"""Generic JSON-backed store base.

Every subsystem in ZOE persists as one JSON file: a list of dataclass rows, or a
dict payload keyed by sender / list name. Before this module each store module
carried ~40 lines of near-identical `_load` / `_save` / `_from_dict` boilerplate;
`Store` centralises corruption tolerance, unknown-key filtering, and atomic
writes so a subsystem only needs its dataclass and its narrow domain helpers.
"""
from dataclasses import asdict, fields
import json
import os
from pathlib import Path
from typing import Callable, Generic, Optional, TypeVar

from app.logging_config import logger

T = TypeVar("T")


class Store(Generic[T]):
    """Reusable JSON-backed store with corruption tolerance, unknown-key
    filtering, and atomic writes. Each subsystem instantiates one, keeps its
    own dataclass, and writes narrow domain functions on top."""

    def __init__(self, path: Callable[[], str], cls: Optional[type[T]] = None) -> None:
        # `path` is a callable so it reads `settings.foo_path` lazily at each
        # call — tests monkeypatch the setting between test runs, and the
        # baked-in path from module import time would otherwise miss the swap.
        # `cls` may be None for raw dict/list stores (conversation.py,
        # conversation_log.py) that only need atomic I/O.
        self._path_fn = path
        self._cls = cls
        self._field_names = {f.name for f in fields(cls)} if cls is not None else set()

    def _path(self) -> Path:
        return Path(self._path_fn())

    def _from_dict(self, d: dict) -> T:
        if self._cls is None:
            # No dataclass configured — raw dict passthrough. Callers with
            # cls=None get the dict back untouched from load_list, and this
            # keeps load_list uniform.
            return d  # type: ignore[return-value]
        # Drop unknown keys so a row from a future version (or a manual edit)
        # can't kill the whole load with a TypeError.
        return self._cls(**{k: v for k, v in d.items() if k in self._field_names})

    def load_list(self) -> list[T]:
        p = self._path()
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            return [self._from_dict(x) for x in raw if isinstance(x, dict)]
        except Exception:
            logger.warning("Could not load %s; starting fresh", p.name)
            return []

    def save_list(self, items: list[T]) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        # cls=None stores hold raw dicts; asdict would fail on those, so pass
        # them through directly.
        if self._cls is None:
            payload = list(items)  # type: ignore[arg-type]
        else:
            payload = [asdict(i) for i in items]
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def load_dict(self) -> dict:
        """For stores that persist a dict-shaped payload (inbound_tracker,
        personal_tasks, lists, conversation, ...). Sub-item filtering is up to
        the caller — pass individual rows through `_from_dict` when they map
        to a dataclass, or handle raw dicts directly."""
        p = self._path()
        if not p.exists():
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            logger.warning("Could not load %s; starting fresh", p.name)
            return {}

    def save_dict(self, data: dict) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
