"""Tests for the list↔expense convert button short-circuit path.

Covers:
- store delete-by-id helpers that revert depends on
- amount-extraction heuristic
- end-to-end `revert.handle` for both directions, including:
  - correct inverse action recorded
  - correct broadcast flag (ledger changes broadcast; list-only doesn't matter
    here since every revert touches the ledger exactly once)
  - stale-id robustness (second tap of a button whose entry is gone)
  - un-parseable list text is restored instead of silently dropped
"""
from app import expenses, lists, revert


def test_lists_remove_by_id(tmp_stores):
    a = lists.add_item("shopping", "milk", "s1")
    b = lists.add_item("shopping", "bread", "s1")
    removed = lists.remove_by_id("shopping", a.id)
    assert removed is not None and removed.id == a.id
    remaining = lists.get_list("shopping")
    assert [i.id for i in remaining] == [b.id]


def test_lists_remove_by_id_missing(tmp_stores):
    assert lists.remove_by_id("shopping", "nope") is None


def test_expenses_delete_by_id(tmp_stores):
    a = expenses.add("s1", 10.0, "סופר", "לא צוין", "milk")
    b = expenses.add("s1", 20.0, "סופר", "לא צוין", "bread")
    removed = expenses.delete_by_id(a.id)
    assert removed is not None and removed.id == a.id
    assert [e.id for e in expenses.list_recent(10)] == [b.id]


def test_expenses_delete_by_id_missing(tmp_stores):
    assert expenses.delete_by_id("nope") is None


def test_parse_amount_leading_number():
    amount, desc = revert._parse_amount("3 חלב")
    assert amount == 3.0 and desc == "חלב"


def test_parse_amount_trailing_number():
    amount, desc = revert._parse_amount("חלב 20")
    assert amount == 20.0 and desc == "חלב"


def test_parse_amount_decimal():
    amount, desc = revert._parse_amount("2.5 חלב 3%")
    assert amount == 2.5 and desc == "חלב 3%"


def test_parse_amount_no_number():
    amount, desc = revert._parse_amount("חלב")
    assert amount is None and desc == "חלב"


def test_handle_list_to_expense(tmp_stores):
    item = lists.add_item("shopping", "3 חלב", "s1")
    outcome = revert.handle("s1", f"revert:list:shopping:{item.id}")
    assert outcome is not None
    reply, broadcast = outcome
    assert broadcast is True
    assert "חלב" in reply
    assert lists.get_list("shopping") == []
    recent = expenses.list_recent(10)
    assert len(recent) == 1
    assert recent[0].amount == 3.0
    assert recent[0].description == "חלב"
    assert recent[0].category == "סופר"
    assert recent[0].sender == "s1"


def test_handle_expense_to_list(tmp_stores):
    e = expenses.add("s1", 50.0, "סופר", "לא צוין", "ענבים")
    outcome = revert.handle("s1", f"revert:expense:{e.id}")
    assert outcome is not None
    reply, broadcast = outcome
    assert broadcast is True
    assert expenses.list_recent(10) == []
    shopping = lists.get_list("shopping")
    assert len(shopping) == 1
    assert shopping[0].text == "50 ענבים"


def test_handle_expense_to_list_no_description(tmp_stores):
    e = expenses.add("s1", 7.0, "סופר", "לא צוין", "")
    outcome = revert.handle("s1", f"revert:expense:{e.id}")
    assert outcome is not None
    shopping = lists.get_list("shopping")
    assert len(shopping) == 1 and shopping[0].text == "7"


def test_handle_stale_list_id(tmp_stores):
    outcome = revert.handle("s1", "revert:list:shopping:ghost")
    assert outcome is not None
    reply, broadcast = outcome
    assert "כבר לא" in reply
    assert broadcast is False


def test_handle_stale_expense_id(tmp_stores):
    outcome = revert.handle("s1", "revert:expense:ghost")
    assert outcome is not None
    reply, broadcast = outcome
    assert "כבר לא" in reply
    assert broadcast is False


def test_handle_unparseable_list_text_restores_item(tmp_stores):
    item = lists.add_item("shopping", "חלב", "s1")
    outcome = revert.handle("s1", f"revert:list:shopping:{item.id}")
    assert outcome is not None
    reply, broadcast = outcome
    assert "לא הצלחתי" in reply
    assert broadcast is False
    # Item must be back on the list so the user isn't silently left short an entry.
    remaining = lists.get_list("shopping")
    assert len(remaining) == 1 and remaining[0].text == "חלב"
    assert expenses.list_recent(10) == []


def test_handle_unknown_payload():
    assert revert.handle("s1", "something-else") is None
    assert revert.handle("s1", "revert:unknown:123") is None


def test_handle_decimal_amount_formats_compactly(tmp_stores):
    e = expenses.add("s1", 2.5, "סופר", "לא צוין", "חלב")
    outcome = revert.handle("s1", f"revert:expense:{e.id}")
    assert outcome is not None
    shopping = lists.get_list("shopping")
    assert len(shopping) == 1 and shopping[0].text == "2.5 חלב"
