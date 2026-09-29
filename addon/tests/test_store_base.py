"""Coverage for `app._store.Store`.

Every subsystem module now delegates its I/O to this class. If any of these
break, every store breaks — so the surface is small on purpose: empty load,
happy roundtrip, corrupt-file tolerance, unknown-key filtering, and atomic
writes surviving a simulated mid-write crash.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app._store import Store


@dataclass
class _Row:
    id: str
    text: str


def _store_at(tmp_path: Path, name: str = "rows.json") -> tuple[Store, Path]:
    path = tmp_path / name
    return Store(lambda: str(path), _Row), path


def test_load_list_missing_file(tmp_path):
    store, _ = _store_at(tmp_path)
    assert store.load_list() == []


def test_load_dict_missing_file(tmp_path):
    store, _ = _store_at(tmp_path)
    assert store.load_dict() == {}


def test_save_and_load_list_roundtrip(tmp_path):
    store, path = _store_at(tmp_path)
    rows = [_Row(id="a", text="one"), _Row(id="b", text="two")]
    store.save_list(rows)
    assert path.exists()
    loaded = store.load_list()
    assert loaded == rows


def test_save_and_load_dict_roundtrip(tmp_path):
    store, path = _store_at(tmp_path)
    payload = {"nir": {"turns": [{"role": "user", "content": "hi"}]}, "wife": {}}
    store.save_dict(payload)
    assert path.exists()
    assert store.load_dict() == payload


def test_load_list_corrupt_returns_empty(tmp_path):
    store, path = _store_at(tmp_path)
    path.write_text("{not valid json", encoding="utf-8")
    assert store.load_list() == []


def test_load_dict_corrupt_returns_empty(tmp_path):
    store, path = _store_at(tmp_path)
    path.write_text("{not valid json", encoding="utf-8")
    assert store.load_dict() == {}


def test_load_list_wrong_shape_returns_empty(tmp_path):
    # A dict where a list is expected shouldn't crash; a caller iterating rows
    # deserves an empty list, not a TypeError.
    store, path = _store_at(tmp_path)
    path.write_text(json.dumps({"x": 1}), encoding="utf-8")
    assert store.load_list() == []


def test_load_dict_wrong_shape_returns_empty(tmp_path):
    store, path = _store_at(tmp_path)
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert store.load_dict() == {}


def test_load_list_drops_non_dict_rows(tmp_path):
    # Defensive: a malformed row of the wrong type shouldn't take out the file.
    store, path = _store_at(tmp_path)
    path.write_text(json.dumps([{"id": "a", "text": "one"}, "junk", 42]), encoding="utf-8")
    assert store.load_list() == [_Row(id="a", text="one")]


def test_load_list_filters_unknown_keys(tmp_path):
    # A row written by a future version with an extra field must still load —
    # the unknown key is silently dropped rather than throwing TypeError.
    store, path = _store_at(tmp_path)
    path.write_text(
        json.dumps([{"id": "a", "text": "one", "future_field": "ignore me"}]),
        encoding="utf-8",
    )
    assert store.load_list() == [_Row(id="a", text="one")]


def test_atomic_write_survives_crash_mid_write(tmp_path, monkeypatch):
    """Simulate a mid-write crash by making `os.replace` raise. The original
    file must still be readable afterwards — the corruption window is what
    atomic writes exist to close."""
    store, path = _store_at(tmp_path)
    store.save_list([_Row(id="a", text="original")])
    assert store.load_list() == [_Row(id="a", text="original")]

    def _boom(src, dst):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr("app._store.os.replace", _boom)
    with pytest.raises(OSError):
        store.save_list([_Row(id="a", text="new"), _Row(id="b", text="added")])
    # Original still intact:
    monkeypatch.undo()
    assert store.load_list() == [_Row(id="a", text="original")]


def test_path_is_read_lazily(tmp_path):
    # Tests rebind `settings.foo_path` between runs; the store must resolve
    # the path each call, not cache it at construction time.
    target = {"value": str(tmp_path / "first.json")}
    store = Store(lambda: target["value"], _Row)
    store.save_list([_Row(id="a", text="one")])

    target["value"] = str(tmp_path / "second.json")
    assert store.load_list() == []  # different file, empty
    store.save_list([_Row(id="b", text="two")])
    assert store.load_list() == [_Row(id="b", text="two")]

    target["value"] = str(tmp_path / "first.json")
    assert store.load_list() == [_Row(id="a", text="one")]


def test_load_dict_without_cls_works(tmp_path):
    # conversation.py / conversation_log.py wrap raw payloads with no dataclass.
    path = tmp_path / "raw.json"
    store = Store(lambda: str(path))
    store.save_dict({"a": 1, "b": [1, 2, 3]})
    assert store.load_dict() == {"a": 1, "b": [1, 2, 3]}
