"""Item 24: grocery tool handlers."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import grocery_secret
from app.grocery_client import (
    GroceryAPIError, GroceryPairingError, GroceryTransportError,
)
from app.handlers.grocery import _handle_grocery_call
from app.settings import settings


SENDER = "972501111111"


@pytest.fixture(autouse=True)
def _per_test(tmp_stores, monkeypatch):
    monkeypatch.setattr(settings, "grocery_secret_path", str(tmp_stores / "g.json"))
    grocery_secret._reset_cache_for_tests()
    yield


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- pairing

class TestPairing:
    def test_pair_stores_token_and_confirms(self):
        with patch("app.handlers.grocery.grocery_client.exchange_code",
                   AsyncMock(return_value="x" * 48)):
            out = _run(_handle_grocery_call(SENDER, "pair_grocery", {"code": "ABCDEFGHIJ"}))
        assert "paired" in out.lower()
        assert grocery_secret.get_token() == "x" * 48
        # Token must NEVER appear in handler response.
        assert "x" * 48 not in out

    def test_pair_empty_code(self):
        out = _run(_handle_grocery_call(SENDER, "pair_grocery", {"code": ""}))
        assert "pairing code" in out.lower()

    def test_pair_rejection_no_auto_retry_message(self):
        with patch("app.handlers.grocery.grocery_client.exchange_code",
                   AsyncMock(side_effect=GroceryPairingError("bad"))):
            out = _run(_handle_grocery_call(SENDER, "pair_grocery", {"code": "ABCDE12345"}))
        assert "fresh code" in out.lower()
        assert grocery_secret.is_paired() is False

    def test_pair_transport_error_surfaced(self):
        with patch("app.handlers.grocery.grocery_client.exchange_code",
                   AsyncMock(side_effect=GroceryTransportError("timeout"))):
            out = _run(_handle_grocery_call(SENDER, "pair_grocery", {"code": "ABCDE12345"}))
        assert "couldn't reach" in out.lower()

    def test_unpair_clears_token(self):
        grocery_secret.set_token("t" * 48)
        out = _run(_handle_grocery_call(SENDER, "unpair_grocery", {}))
        assert "unpaired" in out.lower()
        assert grocery_secret.is_paired() is False

    def test_unpair_when_already_unpaired(self):
        out = _run(_handle_grocery_call(SENDER, "unpair_grocery", {}))
        assert "already unpaired" in out.lower()

    def test_pairing_status(self):
        assert "not paired" in _run(_handle_grocery_call(SENDER, "grocery_pairing_status", {})).lower()
        grocery_secret.set_token("t" * 48)
        assert "paired" in _run(_handle_grocery_call(SENDER, "grocery_pairing_status", {})).lower()


# -------------------------------------------------------- unpaired fallthrough

class TestUnpairedFallthrough:
    """Every list/mutate tool should return the 'please pair' prompt when no
    token is stored — not an exception, not a silent no-op."""

    def test_show_list_without_token(self):
        out = _run(_handle_grocery_call(SENDER, "show_grocery_list", {}))
        assert "paired" in out.lower() or "pair_grocery" in out

    def test_add_without_token(self):
        out = _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "חלב"}))
        assert "paired" in out.lower()

    def test_add_without_token_falls_back_to_internal_list(self, tmp_stores):
        """When the grocery app isn't paired, add_grocery_item still succeeds
        by writing to the internal shopping list so the user doesn't lose the
        intent and so Zoe doesn't compose an ambiguous refusal."""
        from app import lists as internal_lists
        out = _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "חלב", "quantity": 2}))
        assert "✅" in out
        assert "חלב" in out
        # Item actually landed on the internal 'shopping' list.
        shopping = internal_lists.get_list("shopping")
        assert len(shopping) == 1
        assert shopping[0].text == "2 חלב"

    def test_add_without_token_quantity_one_omits_number(self, tmp_stores):
        from app import lists as internal_lists
        _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "לחם"}))
        shopping = internal_lists.get_list("shopping")
        assert len(shopping) == 1 and shopping[0].text == "לחם"


# ---------------------------------------------------------------- mutations

class TestMutations:
    def _prep(self):
        grocery_secret.set_token("t" * 48)

    def test_add_sends_correct_op(self):
        self._prep()
        mock = AsyncMock(return_value={"dedup": False, "state": {}, "totals": {}})
        with patch("app.handlers.grocery.grocery_client.apply_op", mock):
            out = _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "חלב", "quantity": 2}))
        assert "חלב" in out and "✅" in out
        # One call, with addItem + name + quantity.
        mock.assert_awaited_once()
        token, op, key = mock.call_args.args
        assert token == "t" * 48
        assert op == {"type": "addItem", "name": "חלב", "quantity": 2}
        assert 8 <= len(key) <= 80

    def test_add_includes_barcode_when_given(self):
        self._prep()
        mock = AsyncMock(return_value={"dedup": False, "state": {}, "totals": {}})
        with patch("app.handlers.grocery.grocery_client.apply_op", mock):
            _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "x", "barcode": "7290000"}))
        assert mock.call_args.args[1]["barcode"] == "7290000"

    def test_add_empty_name(self):
        self._prep()
        out = _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": ""}))
        assert "name" in out.lower()

    def test_add_defaults_quantity_to_one(self):
        self._prep()
        mock = AsyncMock(return_value={"dedup": False})
        with patch("app.handlers.grocery.grocery_client.apply_op", mock):
            _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "חלב"}))
        assert mock.call_args.args[1]["quantity"] == 1

    def test_remove_sends_remove_op(self):
        self._prep()
        mock = AsyncMock(return_value={"dedup": False})
        with patch("app.handlers.grocery.grocery_client.apply_op", mock):
            _run(_handle_grocery_call(SENDER, "remove_grocery_item", {"id": "abc"}))
        assert mock.call_args.args[1] == {"type": "removeItem", "id": "abc"}

    def test_mark_bought_is_update_checked_true_not_remove(self):
        """Critical distinction — 'סמן X' is checked, NOT removed."""
        self._prep()
        mock = AsyncMock(return_value={"dedup": False})
        with patch("app.handlers.grocery.grocery_client.apply_op", mock):
            _run(_handle_grocery_call(SENDER, "mark_grocery_bought", {"id": "abc"}))
        assert mock.call_args.args[1] == {
            "type": "updateItem", "id": "abc", "changes": {"checked": True},
        }

    def test_update_coerces_fields(self):
        self._prep()
        mock = AsyncMock(return_value={"dedup": False})
        with patch("app.handlers.grocery.grocery_client.apply_op", mock):
            _run(_handle_grocery_call(
                SENDER, "update_grocery_item",
                {"id": "abc", "name": "חדש", "quantity": "3", "actual_agorot": "1250"},
            ))
        changes = mock.call_args.args[1]["changes"]
        assert changes == {"name": "חדש", "quantity": 3, "actualAgorot": 1250}

    def test_update_no_changes(self):
        self._prep()
        out = _run(_handle_grocery_call(SENDER, "update_grocery_item", {"id": "abc"}))
        assert "field" in out.lower() or "change" in out.lower()

    def test_401_during_op_clears_token_and_prompts_repair(self):
        """A revoked token mid-operation should drop local state cleanly, so
        the next command flows into the pairing prompt instead of thrashing
        the API with a dead token."""
        self._prep()
        with patch("app.handlers.grocery.grocery_client.apply_op",
                   AsyncMock(side_effect=GroceryPairingError("revoked"))):
            out = _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "חלב"}))
        assert grocery_secret.is_paired() is False
        assert "paired" in out.lower()

    def test_transport_error_surfaced(self):
        self._prep()
        with patch("app.handlers.grocery.grocery_client.apply_op",
                   AsyncMock(side_effect=GroceryTransportError("timeout"))):
            out = _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "חלב"}))
        assert "couldn't reach" in out.lower()
        # Token must still be paired on transport error — it's a network blip, not a token issue.
        assert grocery_secret.is_paired() is True

    def test_api_error_surfaced(self):
        self._prep()
        err = GroceryAPIError(400, {"error": "invalid_name"})
        with patch("app.handlers.grocery.grocery_client.apply_op",
                   AsyncMock(side_effect=err)):
            out = _run(_handle_grocery_call(SENDER, "add_grocery_item", {"name": "x"}))
        assert "400" in out


# ---------------------------------------------------------------- list render

class TestShowList:
    def test_empty_list(self):
        grocery_secret.set_token("t" * 48)
        with patch("app.handlers.grocery.grocery_client.get_list",
                   AsyncMock(return_value={"items": []})):
            out = _run(_handle_grocery_call(SENDER, "show_grocery_list", {}))
        assert "empty" in out.lower()

    def test_renders_items_with_checked_marker(self):
        grocery_secret.set_token("t" * 48)
        payload = {
            "items": [
                {"id": "1", "name": "חלב", "quantity": 1, "checked": False},
                {"id": "2", "name": "ביצים", "quantity": 12, "checked": True},
            ]
        }
        with patch("app.handlers.grocery.grocery_client.get_list",
                   AsyncMock(return_value=payload)):
            out = _run(_handle_grocery_call(SENDER, "show_grocery_list", {}))
        assert "חלב" in out and "ביצים" in out
        assert "✓" in out  # checked marker for ביצים
        assert "×12" in out  # quantity shown when != 1

    def test_pairing_lost_clears_and_prompts(self):
        grocery_secret.set_token("t" * 48)
        with patch("app.handlers.grocery.grocery_client.get_list",
                   AsyncMock(side_effect=GroceryPairingError("revoked"))):
            out = _run(_handle_grocery_call(SENDER, "show_grocery_list", {}))
        assert grocery_secret.is_paired() is False
        assert "paired" in out.lower()


# ---------------------------------------------------------------- unknown

def test_unknown_tool_returns_empty():
    # Pair first so the unknown-tool falls through PAST the "not paired"
    # guard to the final return "" — otherwise an unknown tool sent to an
    # unpaired instance would ask the user to pair, which is also fine but
    # not what the dispatcher expects (empty = "handler doesn't claim this").
    grocery_secret.set_token("t" * 48)
    assert _run(_handle_grocery_call(SENDER, "no_such_tool", {})) == ""
