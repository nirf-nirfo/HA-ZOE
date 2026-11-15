"""Item 24: HTTP client for the grocery-app assistant API.

Thin async wrapper over httpx with the quirks the brief calls out baked in:

  - `/api/zoe/exchange` is server-to-server; NO bearer required, NO Origin
    header allowed, body is `{"code": "<10-char code>"}`.
  - Other endpoints require `Authorization: Bearer <token>` from the paired
    exchange, and MUST NOT carry an Origin header (the Worker 403s if it
    sees one — a browser-only-defense signal we respect by never setting it).
  - POST /api/zoe/operations takes ONE operation per request as `{"op": {...}}`
    plus a mandatory Idempotency-Key header (8–80 chars).

Every function returns a plain dict/list on success. Errors raise one of:
  - `GroceryPairingError`   (401 on exchange, or any 401/404 on paired calls
                             → likely unpaired, caller should prompt re-pair)
  - `GroceryAPIError`       (any other non-2xx; carries status + body)
  - `GroceryTransportError` (network / timeout; the Worker never saw us)
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.settings import settings

logger = logging.getLogger(__name__)

# Never set Origin — the Worker 403s on it. httpx doesn't add one by default,
# but we re-affirm it by passing an explicit headers dict that omits it.
_USER_AGENT = "ZOE-HA-Addon/grocery"
_TIMEOUT = 30.0


class GroceryError(Exception):
    """Base class for all grocery-client failures."""


class GroceryPairingError(GroceryError):
    """Pairing is bad — token missing, revoked, or exchange code rejected."""


class GroceryAPIError(GroceryError):
    def __init__(self, status: int, body: Any, msg: str = "") -> None:
        super().__init__(msg or f"grocery API {status}")
        self.status = status
        self.body = body


class GroceryTransportError(GroceryError):
    """Network / timeout — the request never got a response."""


def _base_url() -> str:
    return (settings.grocery_base_url or "").rstrip("/")


def _bearer_headers(token: str, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
    }
    if extra:
        h.update(extra)
    return h


async def exchange_code(code: str) -> str:
    """Exchange a 10-char pairing code for a 48-char token.

    Returns the token (caller stores it via grocery_secret.set_token).
    Raises GroceryPairingError on 401 (bad / expired / used / locked code).
    """
    if not code or not code.strip():
        raise ValueError("code must be non-empty")
    url = f"{_base_url()}/api/zoe/exchange"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
                json={"code": code.strip()},
            )
    except httpx.HTTPError as exc:
        raise GroceryTransportError(f"exchange transport: {exc}") from exc
    if resp.status_code == 401:
        # Do NOT retry — 5-wrong-attempts lockout per the brief.
        raise GroceryPairingError("pairing code rejected (invalid / expired / used / locked)")
    if resp.status_code >= 400:
        raise GroceryAPIError(resp.status_code, _safe_body(resp), "exchange failed")
    data = resp.json()
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or len(token) < 32:
        raise GroceryAPIError(resp.status_code, data, "exchange response missing token")
    return token


async def get_list(token: str) -> dict:
    """GET /api/zoe/list → {version, budgetAgorot, store, items, totals}."""
    return await _get(token, "/api/zoe/list")


async def get_history(token: str) -> dict:
    """GET /api/zoe/history → {trips: [...]}, newest first, latest 50 trips."""
    return await _get(token, "/api/zoe/history")


async def apply_op(token: str, op: dict, idempotency_key: str) -> dict:
    """POST /api/zoe/operations with a single op and a mandatory idempotency key.

    Returns the server's response dict (contains `state`, `totals`, `dedup`).
    The caller MUST pass a stable key derived from the inbound message so
    retries produce `dedup: true` instead of duplicating the operation.
    """
    if not isinstance(op, dict) or "type" not in op:
        raise ValueError("op must be a dict with a 'type' field")
    if not (8 <= len(idempotency_key) <= 80):
        raise ValueError("idempotency_key must be 8-80 chars")
    body = {"op": op}  # NOT "ops" — single op per request, per the brief
    url = f"{_base_url()}/api/zoe/operations"
    headers = _bearer_headers(
        token,
        extra={
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise GroceryTransportError(f"operations transport: {exc}") from exc
    return _unwrap(resp)


async def _get(token: str, path: str) -> dict:
    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_bearer_headers(token))
    except httpx.HTTPError as exc:
        raise GroceryTransportError(f"{path} transport: {exc}") from exc
    return _unwrap(resp)


def _unwrap(resp: httpx.Response) -> dict:
    if resp.status_code in (401, 404):
        # 401 = token revoked. 404 = assistant routes disabled (unpaired).
        # Both route to the re-pair user flow.
        raise GroceryPairingError(
            f"grocery auth failed ({resp.status_code}) — re-pair required"
        )
    if resp.status_code >= 400:
        raise GroceryAPIError(resp.status_code, _safe_body(resp))
    try:
        return resp.json()
    except ValueError:
        raise GroceryAPIError(resp.status_code, resp.text, "non-JSON response")


def _safe_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]
