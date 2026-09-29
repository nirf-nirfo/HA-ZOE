import json
from pathlib import Path

from app import lists
from app.settings import settings


def test_empty(tmp_stores):
    assert lists.get_list("shopping") == []
    assert lists.get_all_list_names() == []


def test_add_and_read(tmp_stores):
    i = lists.add_item("shopping", "milk", "s1")
    got = lists.get_list("shopping")
    assert len(got) == 1 and got[0].id == i.id


def test_restart_preserves(tmp_stores):
    lists.add_item("shopping", "x", "s1")
    assert len(lists.get_list("shopping")) == 1


def test_remove_items_substring(tmp_stores):
    lists.add_item("shopping", "buy milk today", "s1")
    lists.add_item("shopping", "bread", "s1")
    removed = lists.remove_items("shopping", "MILK")
    assert removed == ["buy milk today"]
    assert len(lists.get_list("shopping")) == 1


def test_remove_missing_idempotent(tmp_stores):
    assert lists.remove_items("shopping", "nothing") == []


def test_clear_list(tmp_stores):
    lists.add_item("shopping", "a", "s1")
    lists.add_item("shopping", "b", "s1")
    count = lists.clear_list("shopping")
    assert count == 2
    assert lists.get_list("shopping") == []


def test_corrupt_json(tmp_stores):
    Path(settings.lists_path).write_text("garbage", encoding="utf-8")
    assert lists.get_list("shopping") == []


def test_unknown_key_tolerated(tmp_stores):
    data = {
        "shopping": [
            {"id": "i1", "text": "milk", "added_by": "s1", "added_at": 0.0,
             "future_field": "z"},
        ]
    }
    Path(settings.lists_path).write_text(json.dumps(data), encoding="utf-8")
    got = lists.get_list("shopping")
    assert len(got) == 1 and got[0].id == "i1"


def test_get_all_list_names_skips_empty(tmp_stores):
    lists.add_item("shopping", "a", "s1")
    lists.add_item("todo", "b", "s1")
    lists.clear_list("todo")
    got = lists.get_all_list_names()
    assert got == [("shopping", 1)]
