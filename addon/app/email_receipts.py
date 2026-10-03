"""Item 20: extract a household expense from a receipt-shaped email.

Shape:
  extract(msg) -> {"status": "added", "expense_id": ..., ...}
                | {"status": "skipped", "reason": ...}
                | {"status": "not_receipt"}

Flow:
  1. Fast gate — subject / body must contain a receipt-like keyword (Hebrew
     or English). If no keyword matches the model is NOT called at all; the
     function returns `{"status": "not_receipt", "reason": "no_keyword"}`.
     This is the token-budget guard the brief asks for: a mailbox full of
     newsletters never pays an Anthropic bill.
  2. If the gate matches, assemble a vision prompt mirroring the WhatsApp
     photo-of-a-receipt flow (image attachments as image blocks, body text
     as a text block, small PDFs as PDF blocks when the SDK accepts them).
     Ask Claude for one JSON object with the expense fields OR a terse
     "not a receipt" verdict.
  3. On a usable extraction, call `expenses.add(...)` with the primary
     household sender (email isn't attributable to a WhatsApp number).

The function is async because the Anthropic SDK call is run in a worker
thread so it doesn't block the hourly watch loop (Item 21). All failures
are caught and converted to a `skipped` status with a reason string —
Item 20's processor must never bubble an exception out and break iCal
extraction on the same message.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app import claude_agent, expenses
from app.email_backend import EmailMessage
from app.settings import settings

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# Hebrew + English keywords that mark a message as "worth asking the model".
# Chosen to match typical Israeli e-commerce / credit-card confirmations
# without false-firing on marketing. Case-insensitive match on the raw
# subject + body text; a true match still goes through the model, which is
# allowed to say "not a receipt" and skip.
_RECEIPT_KEYWORDS = (
    "חשבונית", "קבלה", "הזמנה", "תשלום", "חיוב",
    "invoice", "receipt", "payment", "order confirmation", "charge",
)

# Soft cap on body text sent to the model. Marketing emails can embed
# multi-KB HTML; we only need the headline / line items to decide. Keeps
# input cost tight on messages that keyword-match but aren't receipts.
_MAX_BODY_CHARS = 8000

# Image attachments larger than this are sent as metadata only (model is
# told "receipt image elided, size X"). Keeps the per-call cost bounded
# when a sender attaches a 5MB phone photo.
_MAX_IMAGE_INLINE_BYTES = 3 * 1024 * 1024

# PDF attachments: send as a PDF content block when under this cap; above
# it, the metadata-only path. The Anthropic SDK accepts PDFs natively via
# the `document` content block.
_MAX_PDF_INLINE_BYTES = 3 * 1024 * 1024

# The receipt extraction model. Opus is the household default (used
# elsewhere in claude_agent for the interactive path when hybrid routing
# is off) — receipt parsing is receipt-sensitive, so we deliberately keep
# this on the stronger model rather than Sonnet.
_RECEIPT_MODEL = claude_agent.INTERACTIVE_MODEL_DEFAULT
_MAX_TOKENS = 512


_RECEIPT_SYSTEM = (
    "You extract one household expense from a receipt-shaped email. "
    "The user is Israeli; prices are in ILS (₪). "
    "Reply with EXACTLY ONE JSON object and nothing else — no prose, no fences, "
    "no commentary. "
    "If the email is NOT a receipt (newsletter, shipping notice without a total, "
    'bank balance alert, generic account email) return {"not_receipt": true} and '
    "nothing else. "
    "Otherwise return an object with keys: "
    '{"amount": number (ILS, grand total), '
    '"category": one of '
    "[\"סופר\",\"מסעדות\",\"דלק\",\"חינוך\",\"בריאות\",\"ביגוד\",\"בית\","
    "\"בילויים\",\"תחבורה\",\"חשבונות\",\"ביטוחים\",\"אחר\"], "
    '"payment_method": one of '
    "[\"MAX\",\"לאומי\",\"PayBox\",\"בינלאומי\",\"מזומן\",\"ביט\",\"לא צוין\"] "
    '(use "לא צוין" if not stated), '
    '"description": short Hebrew/English description of what was bought, '
    '"date": optional ISO YYYY-MM-DD if the receipt date is explicit; omit otherwise}. '
    "Pick the closest category from the fixed list; use 'אחר' ONLY if truly none fit."
)


def _primary_household_sender() -> str:
    """The sender we attribute auto-extracted expenses to.

    Email isn't tied to a WhatsApp number, so we use the first number in
    `allowed_sender_numbers` as a canonical household alias. If none is
    configured (dev / tests without env), fall back to the literal
    "email" so the expense row still saves and attribution is explicit.
    """
    numbers = [
        n.strip() for n in (settings.allowed_sender_numbers or "").split(",") if n.strip()
    ]
    return numbers[0] if numbers else "email"


def _is_receipt_shaped(msg: EmailMessage) -> bool:
    """Fast keyword gate: True if the subject or body hints at a receipt.

    Only a match here warrants an Anthropic call — a mailbox of newsletters
    stays free. The model still gets final say on borderline matches.
    """
    hay = f"{msg.subject}\n{(msg.body_text or '')[:_MAX_BODY_CHARS]}".lower()
    return any(kw.lower() in hay for kw in _RECEIPT_KEYWORDS)


def _attachment_blocks(msg: EmailMessage) -> list[dict[str, Any]]:
    """Build Anthropic content blocks for the attachments worth sending.

    - image/*: inline base64 image block if under the cap, otherwise a
      text note so the model knows an image was elided (not silently dropped).
    - application/pdf: inline base64 document block (the SDK accepts PDFs
      natively for vision/OCR).
    Non-image, non-PDF attachments are ignored — a sketchy .zip or .xml
    doesn't help the OCR gate decide amount.
    """
    out: list[dict[str, Any]] = []
    for att in msg.attachments:
        ctype = (att.content_type or "").lower()
        if not att.content:
            # Already stripped by the backend (over MAX_ATTACHMENT_BYTES).
            out.append(
                {
                    "type": "text",
                    "text": (
                        f"[attachment elided: {att.filename!r} "
                        f"type={ctype} size={att.size}]"
                    ),
                }
            )
            continue
        if ctype.startswith("image/"):
            if len(att.content) > _MAX_IMAGE_INLINE_BYTES:
                out.append(
                    {
                        "type": "text",
                        "text": (
                            f"[image attachment too large to inline: "
                            f"{att.filename!r} size={att.size}]"
                        ),
                    }
                )
                continue
            out.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": ctype,
                        "data": base64.b64encode(att.content).decode("ascii"),
                    },
                }
            )
        elif ctype == "application/pdf":
            if len(att.content) > _MAX_PDF_INLINE_BYTES:
                out.append(
                    {
                        "type": "text",
                        "text": (
                            f"[PDF attachment too large to inline: "
                            f"{att.filename!r} size={att.size}]"
                        ),
                    }
                )
                continue
            out.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(att.content).decode("ascii"),
                    },
                }
            )
    return out


def _build_user_content(msg: EmailMessage) -> list[dict[str, Any]]:
    """Assemble the user turn: receipt attachments first, then the body text."""
    body_excerpt = (msg.body_text or "")[:_MAX_BODY_CHARS]
    header = (
        f"From: {msg.from_addr}\n"
        f"Subject: {msg.subject}\n"
        f"Date: {msg.date.isoformat() if msg.date else ''}\n"
        f"\n"
        f"Body (truncated to {_MAX_BODY_CHARS} chars):\n"
        f"{body_excerpt}"
    )
    blocks: list[dict[str, Any]] = _attachment_blocks(msg)
    blocks.append({"type": "text", "text": header})
    return blocks


# Fenced-JSON tolerance: in case the model wraps the object despite the
# "no fences" instruction, strip a leading ```json / trailing ``` before
# json.loads. Narrowly scoped — we still trust the primary path.
_FENCED_JSON_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.DOTALL)


def _parse_model_output(text: str) -> dict[str, Any] | None:
    cleaned = _FENCED_JSON_RE.sub("", text.strip())
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("email_receipts: model returned non-JSON output: %r", text[:200])
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _coerce_amount(raw: Any) -> float | None:
    try:
        amt = float(raw)
    except (TypeError, ValueError):
        return None
    return amt if amt > 0 else None


def _coerce_date(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except ValueError:
        return None


def _call_model(msg: EmailMessage) -> dict[str, Any] | None:
    """Synchronous Anthropic call, run under asyncio.to_thread by `extract`.

    Isolated so tests can monkeypatch the whole model round-trip in one
    place without having to mock the SDK itself.
    """
    content = _build_user_content(msg)
    resp = claude_agent._client.messages.create(
        model=_RECEIPT_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_RECEIPT_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    parts: list[str] = []
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    joined = "".join(parts).strip()
    if not joined:
        logger.warning("email_receipts: model returned no text content")
        return None
    return _parse_model_output(joined)


async def extract(msg: EmailMessage) -> dict[str, Any]:
    """Receipt-shaped email → maybe one `expenses.add` call.

    Return shapes (all dicts, never raises):
      - {"status": "not_receipt", "reason": "no_keyword"}
          Keyword gate skipped the model.
      - {"status": "not_receipt", "reason": "model_said"}
          Model saw it and said it's not a receipt.
      - {"status": "skipped", "reason": "<...>"}
          Model errored, returned invalid JSON, or missing required fields.
      - {"status": "added", "expense_id": ..., "amount": ..., ...}
          Success — a new row is in `expenses`.
    """
    if not _is_receipt_shaped(msg):
        return {"status": "not_receipt", "reason": "no_keyword"}

    try:
        parsed = await asyncio.to_thread(_call_model, msg)
    except Exception as exc:
        logger.exception("email_receipts: model call failed uid=%s", msg.uid)
        return {"status": "skipped", "reason": f"model_error: {exc.__class__.__name__}"}

    if parsed is None:
        return {"status": "skipped", "reason": "model_bad_json"}

    if parsed.get("not_receipt"):
        return {"status": "not_receipt", "reason": "model_said"}

    amount = _coerce_amount(parsed.get("amount"))
    if amount is None:
        return {"status": "skipped", "reason": "missing_or_bad_amount"}

    category = parsed.get("category")
    if category not in expenses.CATEGORIES:
        category = "אחר"

    payment_method = parsed.get("payment_method") or "לא צוין"
    if payment_method not in expenses.PAYMENT_METHODS:
        payment_method = "לא צוין"

    description = (parsed.get("description") or msg.subject or "").strip()
    date = _coerce_date(parsed.get("date"))

    sender = _primary_household_sender()
    try:
        e = expenses.add(
            sender,
            amount=amount,
            category=category,
            payment_method=payment_method,
            description=description,
            source="receipt",
            date=date,
            raw_message=f"email:{msg.uid}",
        )
    except Exception as exc:
        logger.exception("email_receipts: expenses.add failed uid=%s", msg.uid)
        return {"status": "skipped", "reason": f"store_error: {exc.__class__.__name__}"}

    logger.info(
        "email_receipts: added expense id=%s amount=%.2f for email uid=%s",
        e.id,
        e.amount,
        msg.uid,
    )
    return {
        "status": "added",
        "expense_id": e.id,
        "amount": e.amount,
        "category": e.category,
        "payment_method": e.payment_method,
        "description": e.description,
        "date": e.date,
    }
