"""Item 20: orchestrator that runs both extractors over one email.

Shape:
  process_email(msg) ->
    {"status": "disabled" | "processed", "receipt": {...}, "ical": {...}}

Called from two places:
  - Item 21's hourly watch loop (future) — gated on `settings.email_auto_extract`.
  - The `process_email_now(uid)` tool — manual trigger from WhatsApp; the
    tool handler bypasses the flag by passing `force=True` so the user can
    smoke-test the extractors from the chat without flipping the add-on
    option first.

Side effects of a "processed" run:
  - On a receipt-add: broadcast a short Hebrew summary of the expense to
    the whole household, mirroring the expense broadcast path in
    `handlers/expense` / `agent_loop._broadcast`. Expenses are
    household-wide; the broadcast is the user-visible signal that an
    email just moved money in the ledger.
  - On an iCal-add: private per-sender message to the primary household
    sender only. Agenda is per-sender, and the brief explicitly asks for
    the "don't DM the wife" behavior so the Meta 24h window on her number
    isn't a factor.

A failure in either extractor is contained — the processor swallows the
exception, logs it, and still runs the other extractor. This keeps the
watch loop idempotent per message: a bad receipt parse doesn't orphan the
iCal events on the same invite.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import email_ical, email_receipts
from app.email_backend import EmailMessage
from app.handlers._common import _fmt_ils
from app.logging_config import logger as _default_logger
from app.settings import settings
from app.whatsapp import send_message

logger = logging.getLogger(__name__)
# Reuse the shared logger too so processor noise lands in the same place
# as the rest of ZOE's logs; `_default_logger` is unused directly here but
# imported to document the intent.
_ = _default_logger


def _allowed_senders() -> list[str]:
    return [
        n.strip()
        for n in (settings.allowed_sender_numbers or "").split(",")
        if n.strip()
    ]


def _primary_sender() -> str | None:
    numbers = _allowed_senders()
    return numbers[0] if numbers else None


def _format_receipt_broadcast(result: dict[str, Any]) -> str:
    """Build the Hebrew expense summary line broadcast to the household.

    Mirrors `handlers.expense`'s add_expense reply shape so the message
    the household sees from an email receipt is visually identical to one
    the user logged by hand.
    """
    amount = result.get("amount", 0)
    category = result.get("category", "")
    payment = result.get("payment_method", "")
    description = result.get("description", "")
    expense_id = result.get("expense_id", "")
    payment_note = f" ({payment})" if payment and payment != "לא צוין" else ""
    desc_note = f" — {description}" if description else ""
    return (
        f"\U0001F4E7 נרשם מהמייל: {_fmt_ils(amount)} · {category}{payment_note}"
        f"{desc_note} [id: {expense_id}]"
    )


def _format_ical_notification(result: dict[str, Any]) -> str:
    added = result.get("events_added", 0)
    events = result.get("events", []) or []
    if added == 1 and events:
        ev = events[0]
        return f"\U0001F4C5 נוסף לסדר-היום של {ev.get('date')}: {ev.get('text')}"
    if events:
        lines = [f"\U0001F4C5 נוספו {added} אירועים לסדר-היום:"]
        for ev in events:
            lines.append(f"• {ev.get('date')} — {ev.get('text')}")
        return "\n".join(lines)
    return f"\U0001F4C5 נוספו {added} אירועים לסדר-היום מהמייל."


async def _broadcast_to_household(text: str) -> None:
    """Send `text` to every allowed sender. Silent per-recipient failure
    so a closed 24h window on one number can't suppress the other.
    """
    for phone in _allowed_senders():
        try:
            await send_message(phone, text)
        except Exception:
            logger.exception(
                "email_processor: broadcast to %s failed (24h window closed?)",
                phone,
            )


async def _notify_primary(text: str) -> None:
    primary = _primary_sender()
    if not primary:
        logger.info(
            "email_processor: no primary sender configured; skipping iCal notification"
        )
        return
    try:
        await send_message(primary, text)
    except Exception:
        logger.exception(
            "email_processor: iCal notification to %s failed", primary
        )


async def process_email(msg: EmailMessage, *, force: bool = False) -> dict[str, Any]:
    """Run both extractors on one fully-loaded EmailMessage.

    By default, obeys `settings.email_auto_extract` — flag OFF returns
    `{"status": "disabled"}` without touching the extractors or any store.
    `force=True` (passed by the manual `process_email_now` tool) bypasses
    the flag so the user can test the flow from WhatsApp.

    The returned dict always carries two subfields on a "processed" run:
    `receipt` is whatever `email_receipts.extract` returned, and `ical`
    is whatever `email_ical.extract` returned — opaque to callers; the
    `status` field inside each is the one to branch on.
    """
    if not force and not settings.email_auto_extract:
        return {"status": "disabled"}

    # Receipt first — if the receipt gate skips (keyword miss), no model
    # call happens. iCal runs regardless; an email can carry both a
    # calendar invite AND a receipt (rare, but possible — e.g. an event
    # ticket invoice).
    try:
        receipt_result = await email_receipts.extract(msg)
    except Exception as exc:
        logger.exception("email_processor: receipt extract crashed uid=%s", msg.uid)
        receipt_result = {
            "status": "skipped",
            "reason": f"crash: {exc.__class__.__name__}",
        }

    try:
        ical_result = await asyncio.to_thread(email_ical.extract, msg)
    except Exception as exc:
        logger.exception("email_processor: ical extract crashed uid=%s", msg.uid)
        ical_result = {
            "status": "skipped",
            "reason": f"crash: {exc.__class__.__name__}",
        }

    if receipt_result.get("status") == "added":
        await _broadcast_to_household(_format_receipt_broadcast(receipt_result))

    if ical_result.get("status") == "added" and ical_result.get("events_added"):
        await _notify_primary(_format_ical_notification(ical_result))

    return {
        "status": "processed",
        "receipt": receipt_result,
        "ical": ical_result,
    }
