"""Item 24: grocery API client.

Mocks httpx.AsyncClient at the module level. Verifies: request shapes,
header requirements (bearer, idempotency-key, no Origin), status handling
(401 → pairing error, 404 → pairing error, 5xx → API error, transport
error → transport error), and response parsing.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app import grocery_client
from app.grocery_client import (
    GroceryAPIError, GroceryPairingError, GroceryTransportError,
)

BASE = grocery_client._base_url()


def _resp(status: int, body) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    if isinstance(body, (dict, list)):
        r.json.return_value = body
        r.text = json.dumps(body)
    else:
        r.json.side_effect = ValueError("not json")
        r.text = str(body)
    return r


class _FakeClient:
    """httpx.AsyncClient stand-in that records calls and returns a scripted response."""
    last: dict = {}
    reply: MagicMock | None = None
    raise_transport: Exception | None = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, **kw):
        if type(self).raise_transport:
            raise type(self).raise_transport
        type(self).last = {"method": "POST", "url": url, "headers": headers, "json": json}
        return type(self).reply

    async def get(self, url, headers=None, **kw):
        if type(self).raise_transport:
            raise type(self).raise_transport
        type(self).last = {"method": "GET", "url": url, "headers": headers}
        return type(self).reply


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeClient.last = {}
    _FakeClient.reply = None
    _FakeClient.raise_transport = None
    yield


def _run(coro):
    return asyncio.run(coro)


class TestExchange:
    def test_posts_json_no_bearer_no_origin(self):
        _FakeClient.reply = _resp(200, {"token": "x" * 48})
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            token = _run(grocery_client.exchange_code("ABCDE12345"))
        assert token == "x" * 48
        assert _FakeClient.last["url"] == f"{BASE}/api/zoe/exchange"
        assert _FakeClient.last["json"] == {"code": "ABCDE12345"}
        # No bearer, no Origin.
        assert "Authorization" not in _FakeClient.last["headers"]
        assert "Origin" not in _FakeClient.last["headers"]
        assert _FakeClient.last["headers"]["Content-Type"] == "application/json"

    def test_401_raises_pairing_error_no_retry(self):
        _FakeClient.reply = _resp(401, {"error": "bad code"})
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(GroceryPairingError):
                _run(grocery_client.exchange_code("ABCDE12345"))

    def test_malformed_token_response_raises_api_error(self):
        _FakeClient.reply = _resp(200, {"nope": True})
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(GroceryAPIError):
                _run(grocery_client.exchange_code("ABCDE12345"))

    def test_transport_error_wrapped(self):
        _FakeClient.raise_transport = httpx.ConnectError("boom")
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(GroceryTransportError):
                _run(grocery_client.exchange_code("ABCDE12345"))

    def test_empty_code_rejected_locally(self):
        with pytest.raises(ValueError):
            _run(grocery_client.exchange_code("   "))


class TestApplyOp:
    def test_single_op_wrapped_with_idem_key_header(self):
        _FakeClient.reply = _resp(200, {"state": {}, "totals": {}, "dedup": False})
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            out = _run(grocery_client.apply_op(
                "tok" * 16,
                {"type": "addItem", "name": "חלב"},
                "a" * 40,
            ))
        assert out == {"state": {}, "totals": {}, "dedup": False}
        assert _FakeClient.last["url"] == f"{BASE}/api/zoe/operations"
        # Single op, NOT an "ops" array.
        assert _FakeClient.last["json"] == {"op": {"type": "addItem", "name": "חלב"}}
        assert _FakeClient.last["headers"]["Authorization"].startswith("Bearer ")
        assert _FakeClient.last["headers"]["Idempotency-Key"] == "a" * 40
        # Origin never set.
        assert "Origin" not in _FakeClient.last["headers"]

    def test_401_clears_to_pairing_error(self):
        _FakeClient.reply = _resp(401, {"error": "bad token"})
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(GroceryPairingError):
                _run(grocery_client.apply_op("t" * 48, {"type": "addItem", "name": "x"}, "k" * 40))

    def test_404_clears_to_pairing_error(self):
        """Unpaired state (API disabled) surfaces as 404; treat it as pairing lost."""
        _FakeClient.reply = _resp(404, {"error": "disabled"})
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(GroceryPairingError):
                _run(grocery_client.apply_op("t" * 48, {"type": "addItem", "name": "x"}, "k" * 40))

    def test_400_surfaces_as_api_error(self):
        _FakeClient.reply = _resp(400, {"error": "invalid_name"})
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(GroceryAPIError) as exc:
                _run(grocery_client.apply_op("t" * 48, {"type": "addItem", "name": ""}, "k" * 40))
        assert exc.value.status == 400

    def test_bad_idem_key_rejected_locally(self):
        with pytest.raises(ValueError):
            _run(grocery_client.apply_op("t" * 48, {"type": "addItem", "name": "x"}, "short"))

    def test_bad_op_rejected_locally(self):
        with pytest.raises(ValueError):
            _run(grocery_client.apply_op("t" * 48, {"name": "x"}, "k" * 40))


class TestGetList:
    def test_bearer_and_json(self):
        payload = {"version": 1, "items": [{"id": "a", "name": "חלב"}], "totals": {}}
        _FakeClient.reply = _resp(200, payload)
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            out = _run(grocery_client.get_list("t" * 48))
        assert out == payload
        assert _FakeClient.last["url"] == f"{BASE}/api/zoe/list"
        assert _FakeClient.last["headers"]["Authorization"] == "Bearer " + "t" * 48
        assert "Origin" not in _FakeClient.last["headers"]

    def test_pairing_lost_on_401(self):
        _FakeClient.reply = _resp(401, "")
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(GroceryPairingError):
                _run(grocery_client.get_list("t" * 48))


class TestGetHistory:
    def test_shape_passthrough(self):
        payload = {"trips": [{"id": "t1", "endedAt": 1_700_000_000}]}
        _FakeClient.reply = _resp(200, payload)
        with patch.object(grocery_client.httpx, "AsyncClient", _FakeClient):
            out = _run(grocery_client.get_history("t" * 48))
        assert out == payload
        assert _FakeClient.last["url"] == f"{BASE}/api/zoe/history"
