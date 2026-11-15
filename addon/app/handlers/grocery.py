"""Item 24: grocery-app tool handlers.

Seven agent tools:
  - `pair_grocery(code)`           — exchange a 10-char pairing code
  - `unpair_grocery()`             — clear local token
  - `grocery_pairing_status()`     — paired / unpaired, no secrets ever
  - `show_grocery_list()`          — GET /api/zoe/list (requires pairing)
  - `add_grocery_item(name, qty?, barcode?)`
  - `remove_grocery_item(id)`      — explicit delete only
  - `mark_grocery_bought(id)`      — updateItem checked=true
  - `update_grocery_item(id, ...)` — rename / requantify / set actual price

Idempotency keys for every mutating op are derived in
`grocery_idempotency.key_for(sender, op)` so a model self-retry inside a
turn hits the server with the same key and dedups cleanly.

Pairing / unpaired state is treated as a user-fixable condition: a 401
or 404 from the API clears the local token and the handler returns a
clear "please pair again" prompt the agent paraphrases to the sender.
"""
from __future__ import annotations

import logging

from app import grocery_client, grocery_secret
from app.grocery_client import (
    GroceryAPIError, GroceryPairingError, GroceryTransportError,
)
from app.grocery_idempotency import key_for

logger = logging.getLogger(__name__)


def _not_paired() -> str:
    return (
        "The grocery app isn't paired yet. Open it → הגדרות → 'צור קוד חיבור', "
        "then send the 10-character code here with: `pair_grocery <code>`."
    )


async def _handle_grocery_call(sender: str, tool: str, inp: dict) -> str:
    # -------- pairing -----------------------------------------------------
    if tool == "pair_grocery":
        code = (inp.get("code") or "").strip()
        if not code:
            return "I need the 10-character pairing code from the grocery app."
        try:
            token = await grocery_client.exchange_code(code)
        except GroceryPairingError:
            # The server enforces a 5-attempt lockout — do NOT auto-retry.
            return (
                "That pairing code was rejected (invalid, expired, already used, "
                "or the 5-attempt lock is tripped). Please generate a fresh code "
                "in the grocery app and try again."
            )
        except GroceryTransportError as exc:
            return f"Couldn't reach the grocery app right now: {exc}"
        except GroceryAPIError as exc:
            return f"Pairing failed ({exc.status})."
        grocery_secret.set_token(token)
        return "Grocery app paired ✅"

    if tool == "unpair_grocery":
        had = grocery_secret.clear()
        return "Grocery app unpaired." if had else "Was already unpaired."

    if tool == "grocery_pairing_status":
        return "Grocery app: paired ✅" if grocery_secret.is_paired() else "Grocery app: not paired."

    # -------- paired-only operations below --------------------------------
    token = grocery_secret.get_token()
    if token is None:
        return _not_paired()

    if tool == "show_grocery_list":
        try:
            data = await grocery_client.get_list(token)
        except GroceryPairingError:
            grocery_secret.clear()
            return _not_paired()
        except (GroceryTransportError, GroceryAPIError) as exc:
            return f"Couldn't fetch the list: {exc}"
        items = data.get("items") or []
        if not items:
            return "The grocery list is empty."
        lines = [f"Grocery list ({len(items)} items):"]
        for it in items:
            mark = "✓" if it.get("checked") else "•"
            qty = it.get("quantity") or 1
            lines.append(
                f"{mark} [{it.get('id','?')}] {it.get('name','?')}"
                + (f" ×{qty}" if qty != 1 else "")
            )
        return "\n".join(lines)

    if tool == "add_grocery_item":
        name = (inp.get("name") or "").strip()
        if not name:
            return "I need a name for the item."
        try:
            qty = int(inp.get("quantity", 1))
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            qty = 1
        op: dict = {"type": "addItem", "name": name, "quantity": qty}
        barcode = (inp.get("barcode") or "").strip()
        if barcode:
            op["barcode"] = barcode
        return await _apply(sender, token, op, success=f"Added {name} ×{qty} ✅")

    if tool == "remove_grocery_item":
        item_id = (inp.get("id") or "").strip()
        if not item_id:
            return "I need the item id to remove (see show_grocery_list for ids)."
        op = {"type": "removeItem", "id": item_id}
        return await _apply(sender, token, op, success="Removed ✅")

    if tool == "mark_grocery_bought":
        item_id = (inp.get("id") or "").strip()
        if not item_id:
            return "I need the item id to check off (see show_grocery_list for ids)."
        op = {"type": "updateItem", "id": item_id, "changes": {"checked": True}}
        return await _apply(sender, token, op, success="Checked off ✅")

    if tool == "update_grocery_item":
        item_id = (inp.get("id") or "").strip()
        if not item_id:
            return "I need the item id to update."
        changes: dict = {}
        if (name := (inp.get("name") or "").strip()):
            changes["name"] = name
        if "quantity" in inp:
            try:
                changes["quantity"] = int(inp["quantity"])
            except (TypeError, ValueError):
                pass
        if "actual_agorot" in inp:
            try:
                changes["actualAgorot"] = int(inp["actual_agorot"])
            except (TypeError, ValueError):
                pass
        if "checked" in inp:
            changes["checked"] = bool(inp["checked"])
        if not changes:
            return "I need at least one field to change (name, quantity, actual_agorot, checked)."
        op = {"type": "updateItem", "id": item_id, "changes": changes}
        return await _apply(sender, token, op, success="Updated ✅")

    return ""


async def _apply(sender: str, token: str, op: dict, *, success: str) -> str:
    """Wrapper around grocery_client.apply_op that handles the common error
    paths uniformly and reports a dedup hit as the same success message
    (the server considers it 'already applied' which is what the user wants)."""
    key = key_for(sender, op)
    try:
        resp = await grocery_client.apply_op(token, op, key)
    except GroceryPairingError:
        grocery_secret.clear()
        return _not_paired()
    except GroceryTransportError as exc:
        return f"Couldn't reach the grocery app: {exc}"
    except GroceryAPIError as exc:
        body = exc.body if isinstance(exc.body, str) else str(exc.body)[:200]
        return f"Grocery API refused ({exc.status}): {body}"
    if isinstance(resp, dict) and resp.get("dedup"):
        logger.info("grocery op %s dedup hit (key=%s)", op.get("type"), key)
    return success
