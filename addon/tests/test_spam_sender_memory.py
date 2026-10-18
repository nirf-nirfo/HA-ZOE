"""Item 22: SenderMemory store."""
from app import spam_sender_memory


def test_set_and_get(tmp_stores):
    spam_sender_memory.set_verdict("promos@shop.com", "spam")
    rec = spam_sender_memory.get("promos@shop.com")
    assert rec is not None and rec.verdict == "spam"


def test_lookup_normalizes_case_and_whitespace(tmp_stores):
    spam_sender_memory.set_verdict("Mom@Example.com", "legit")
    assert spam_sender_memory.get("  mom@example.com  ").verdict == "legit"


def test_record_hit_increments(tmp_stores):
    spam_sender_memory.set_verdict("x@y.com", "spam")
    spam_sender_memory.record_hit("x@y.com")
    spam_sender_memory.record_hit("x@y.com")
    assert spam_sender_memory.get("x@y.com").hits == 2


def test_record_hit_on_unknown_is_noop(tmp_stores):
    spam_sender_memory.record_hit("never@seen.com")
    assert spam_sender_memory.get("never@seen.com") is None


def test_forget(tmp_stores):
    spam_sender_memory.set_verdict("x@y.com", "spam")
    assert spam_sender_memory.forget("x@y.com") is True
    assert spam_sender_memory.get("x@y.com") is None
    assert spam_sender_memory.forget("x@y.com") is False


def test_count(tmp_stores):
    assert spam_sender_memory.count() == 0
    spam_sender_memory.set_verdict("a@a.com", "spam")
    spam_sender_memory.set_verdict("b@b.com", "legit")
    assert spam_sender_memory.count() == 2
