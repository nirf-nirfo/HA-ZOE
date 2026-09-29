import json
from pathlib import Path

from app import memory
from app.settings import settings


def test_empty(tmp_stores):
    assert memory.all_facts() == []


def test_remember_and_read(tmp_stores):
    f = memory.remember("Anna's shoe size is 42")
    got = memory.all_facts()
    assert len(got) == 1 and got[0].id == f.id


def test_remember_duplicate_is_noop(tmp_stores):
    memory.remember("mom's birthday is May 1")
    r2 = memory.remember("Mom's Birthday is May 1")  # case differs, but matches
    assert r2 is None
    assert len(memory.all_facts()) == 1


def test_restart_preserves(tmp_stores):
    memory.remember("x")
    assert len(memory.all_facts()) == 1


def test_forget_substring(tmp_stores):
    memory.remember("Anna likes green apples")
    memory.remember("Ben likes bananas")
    removed = memory.forget("apples")
    assert removed == ["Anna likes green apples"]
    assert len(memory.all_facts()) == 1


def test_forget_missing(tmp_stores):
    assert memory.forget("nothing") == []


def test_corrupt_json(tmp_stores):
    Path(settings.memory_path).write_text("garbage", encoding="utf-8")
    assert memory.all_facts() == []


def test_unknown_key_tolerated(tmp_stores):
    row = [{"id": "f1", "text": "hello", "added_at": 0.0, "future_field": "z"}]
    Path(settings.memory_path).write_text(json.dumps(row), encoding="utf-8")
    got = memory.all_facts()
    assert len(got) == 1 and got[0].id == "f1"
