import json
import time
from pathlib import Path

from app import conversation_log
from app.settings import settings


def test_empty(tmp_stores):
    assert conversation_log.search("s1", "anything") == []


def test_append_and_search(tmp_stores):
    conversation_log.append("s1", "how much did I spend on gas", "300 ILS")
    got = conversation_log.search("s1", "gas")
    assert len(got) == 1
    assert got[0]["user"] == "how much did I spend on gas"


def test_restart_preserves(tmp_stores):
    conversation_log.append("s1", "u", "a")
    assert len(conversation_log.search("s1", "u")) == 1


def test_sender_isolation_in_search(tmp_stores):
    conversation_log.append("s1", "mine", "reply")
    conversation_log.append("s2", "mine", "reply")
    got = conversation_log.search("s1", "mine")
    assert len(got) == 1


def test_ninety_day_prune_on_append(tmp_stores):
    """Anything older than 90 days is dropped when a new entry is appended."""
    old = [{
        "sender": "s1",
        "ts": time.time() - 91 * 24 * 3600,
        "user": "ancient",
        "assistant": "reply",
    }]
    Path(settings.conversation_log_path).write_text(json.dumps(old), encoding="utf-8")
    conversation_log.append("s1", "fresh", "reply")
    got = conversation_log.search("s1", "ancient")
    assert got == []
    got_fresh = conversation_log.search("s1", "fresh")
    assert len(got_fresh) == 1


def test_days_back_filter(tmp_stores):
    now = time.time()
    entries = [
        {"sender": "s1", "ts": now - 5 * 86400, "user": "recent thing", "assistant": "a"},
        {"sender": "s1", "ts": now - 40 * 86400, "user": "recent thing", "assistant": "a"},
    ]
    Path(settings.conversation_log_path).write_text(json.dumps(entries), encoding="utf-8")
    got = conversation_log.search("s1", "recent", days_back=7)
    assert len(got) == 1


def test_corrupt_json(tmp_stores):
    Path(settings.conversation_log_path).write_text("garbage", encoding="utf-8")
    assert conversation_log.search("s1", "x") == []


def test_search_empty_query(tmp_stores):
    conversation_log.append("s1", "hello", "hi")
    assert conversation_log.search("s1", "  ") == []


def test_search_results_capped(tmp_stores):
    for i in range(30):
        conversation_log.append("s1", f"hit {i}", "a")
    got = conversation_log.search("s1", "hit")
    assert len(got) == 15  # _MAX_RESULTS
