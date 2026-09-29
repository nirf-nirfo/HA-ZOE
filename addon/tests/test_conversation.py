import time

from app import conversation


def test_empty_returns_empty(tmp_stores):
    assert conversation.recent("s1") == []


def test_record_and_recent_roundtrip(tmp_stores):
    conversation.record("s1", "hi", "hello")
    got = conversation.recent("s1")
    assert got == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_restart_preserves(tmp_stores):
    conversation.record("s1", "hi", "hello")
    assert len(conversation.recent("s1")) == 2


def test_ttl_expiry(tmp_stores, monkeypatch):
    """After the 24h TTL, recent() returns [] even though the entry is on disk."""
    real_time = time.time
    # First record at t=0.
    monkeypatch.setattr(conversation.time, "time", lambda: 1_000_000.0)
    conversation.record("s1", "hi", "hello")
    # Now 25 hours later.
    monkeypatch.setattr(conversation.time, "time", lambda: 1_000_000.0 + 25 * 3600)
    assert conversation.recent("s1") == []


def test_rolling_cap(tmp_stores):
    for i in range(25):
        conversation.record("s1", f"u{i}", f"a{i}")
    got = conversation.recent("s1")
    # _MAX_MESSAGES == 40 -> 20 full exchanges kept.
    assert len(got) == 40
    # Oldest kept should be exchange 5.
    assert got[0]["content"] == "u5"


def test_multiple_senders_isolated(tmp_stores):
    conversation.record("s1", "hi1", "a1")
    conversation.record("s2", "hi2", "a2")
    assert [t["content"] for t in conversation.recent("s1")] == ["hi1", "a1"]
    assert [t["content"] for t in conversation.recent("s2")] == ["hi2", "a2"]
