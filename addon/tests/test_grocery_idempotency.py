"""Item 24: idempotency-key derivation.

The contract we're testing:
  - stable per (sender, op, utc_minute) tuple
  - different ops in same minute → different keys
  - same op in next minute → different key (acceptable per the comment)
  - within 8-80 char range the server requires
"""
from app.grocery_idempotency import key_for


SENDER = "972501111111"


def test_key_is_in_server_range():
    k = key_for(SENDER, {"type": "addItem", "name": "חלב"}, utc_minute=1000)
    assert 8 <= len(k) <= 80


def test_same_tuple_same_key():
    op = {"type": "addItem", "name": "חלב", "quantity": 2}
    k1 = key_for(SENDER, op, utc_minute=500)
    k2 = key_for(SENDER, op, utc_minute=500)
    assert k1 == k2


def test_different_op_different_key():
    k_milk = key_for(SENDER, {"type": "addItem", "name": "חלב"}, utc_minute=500)
    k_eggs = key_for(SENDER, {"type": "addItem", "name": "ביצים"}, utc_minute=500)
    assert k_milk != k_eggs


def test_different_sender_different_key():
    op = {"type": "addItem", "name": "חלב"}
    k_a = key_for("972501111111", op, utc_minute=500)
    k_b = key_for("972502222222", op, utc_minute=500)
    assert k_a != k_b


def test_next_minute_different_key():
    op = {"type": "addItem", "name": "חלב"}
    assert key_for(SENDER, op, utc_minute=500) != key_for(SENDER, op, utc_minute=501)


def test_dict_key_order_independent():
    """json.dumps(sort_keys=True) guarantees the hash is stable regardless of
    how the agent passes the dict in."""
    k1 = key_for(SENDER, {"name": "חלב", "type": "addItem", "quantity": 2}, utc_minute=500)
    k2 = key_for(SENDER, {"quantity": 2, "type": "addItem", "name": "חלב"}, utc_minute=500)
    assert k1 == k2


def test_unicode_hebrew_name_stable():
    """Hebrew names must hash deterministically — no accidental mis-encoding."""
    op = {"type": "addItem", "name": "חלב תנובה 3%"}
    k1 = key_for(SENDER, op, utc_minute=500)
    k2 = key_for(SENDER, op, utc_minute=500)
    assert k1 == k2 and len(k1) == 40
