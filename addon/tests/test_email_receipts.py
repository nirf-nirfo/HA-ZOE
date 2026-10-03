"""Tests for the receipt extractor (Item 20).

The Anthropic client is monkey-patched at `claude_agent._client` with a
MagicMock whose `messages.create` returns a canned response object. This
mirrors the pattern test_email_tools.py uses to stub the IMAP backend —
test the surface, not the SDK plumbing.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import claude_agent, email_receipts, expenses
from app.email_backend import EmailAttachment, EmailMessage
from app.settings import settings


def _run(coro):
    return asyncio.run(coro)


def _msg(
    *,
    uid: str = "u1",
    subject: str = "קבלה",
    body: str = "Total ILS 150",
    attachments: list[EmailAttachment] | None = None,
) -> EmailMessage:
    return EmailMessage(
        uid=uid,
        from_addr="shop@example.com",
        to_addrs=["me@example.com"],
        subject=subject,
        date=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        snippet=body[:200],
        body_text=body,
        attachments=list(attachments or []),
    )


def _stub_model_response(payload: dict | str) -> MagicMock:
    """Fake Anthropic resp: `.content[0].text` = JSON-serialised payload."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], usage=None)


@pytest.fixture
def household_sender(monkeypatch):
    monkeypatch.setattr(settings, "allowed_sender_numbers", "972501234567,972509999999")
    yield "972501234567"


@pytest.fixture
def stub_anthropic(monkeypatch):
    """Replace `_client.messages.create` with a MagicMock. Tests inject the
    canned return via `stub_anthropic.create.return_value = _stub_model_response(...)`.
    """
    fake_client = MagicMock()
    fake_client.messages.create = MagicMock()
    monkeypatch.setattr(claude_agent, "_client", fake_client)
    return fake_client.messages


# ------------------------------------------------- fast-gate behaviour


def test_gate_skips_non_receipt_subject_without_model_call(
    tmp_stores, household_sender, stub_anthropic
):
    msg = _msg(subject="Newsletter: ten tips", body="hello world nothing interesting")
    result = _run(email_receipts.extract(msg))
    assert result == {"status": "not_receipt", "reason": "no_keyword"}
    # The model was never consulted — tokens stay free on newsletters.
    stub_anthropic.create.assert_not_called()
    # No row written to the expense store.
    assert expenses.list_recent(10) == []


def test_hebrew_keyword_hits_gate(tmp_stores, household_sender, stub_anthropic):
    # The gate must match on Hebrew receipt words — the household speaks Hebrew.
    stub_anthropic.create.return_value = _stub_model_response({"not_receipt": True})
    msg = _msg(subject="חשבונית מספר 12345", body="no body content")
    result = _run(email_receipts.extract(msg))
    assert result["status"] == "not_receipt"
    assert result["reason"] == "model_said"
    stub_anthropic.create.assert_called_once()


# ------------------------------------------------- happy path


def test_receipt_extraction_adds_expense(tmp_stores, household_sender, stub_anthropic):
    stub_anthropic.create.return_value = _stub_model_response(
        {
            "amount": 150,
            "category": "סופר",
            "payment_method": "MAX",
            "description": "קניות שבועיות",
            "date": "2026-10-01",
        }
    )
    msg = _msg(subject="קבלה סופר X", body="Total 150 ILS, paid with MAX")

    result = _run(email_receipts.extract(msg))

    assert result["status"] == "added"
    assert result["amount"] == 150
    assert result["category"] == "סופר"
    assert result["payment_method"] == "MAX"
    assert result["description"] == "קניות שבועיות"
    assert result["date"] == "2026-10-01"
    assert "expense_id" in result

    # Expense is in the store, attributed to the primary household sender,
    # and tagged source=receipt (not manual) so delete_last/receipt handling
    # treats it correctly.
    stored = expenses.list_recent(10)
    assert len(stored) == 1
    e = stored[0]
    assert e.sender == household_sender
    assert e.amount == 150.0
    assert e.category == "סופר"
    assert e.payment_method == "MAX"
    assert e.source == "receipt"
    assert e.description == "קניות שבועיות"
    assert e.date == "2026-10-01"


def test_model_said_not_receipt_returns_skip(
    tmp_stores, household_sender, stub_anthropic
):
    # Subject keyword matches, but on inspection the model decides it isn't
    # a receipt — must NOT add an expense.
    stub_anthropic.create.return_value = _stub_model_response({"not_receipt": True})
    msg = _msg(
        subject="payment reminder for your subscription",
        body="your upcoming payment is scheduled for next month",
    )

    result = _run(email_receipts.extract(msg))

    assert result["status"] == "not_receipt"
    assert result["reason"] == "model_said"
    assert expenses.list_recent(10) == []


# ---------------------------------------------- failure-mode coverage


def test_model_bad_json_returns_skip(tmp_stores, household_sender, stub_anthropic):
    stub_anthropic.create.return_value = _stub_model_response("not valid json at all {{{")
    msg = _msg(subject="invoice XYZ", body="total 50 shekel")
    result = _run(email_receipts.extract(msg))
    assert result["status"] == "skipped"
    assert result["reason"] == "model_bad_json"
    assert expenses.list_recent(10) == []


def test_missing_amount_returns_skip(tmp_stores, household_sender, stub_anthropic):
    # Model returned a receipt-shaped object but no amount — the store write
    # must be skipped rather than defaulting to 0.
    stub_anthropic.create.return_value = _stub_model_response(
        {"category": "סופר", "payment_method": "MAX", "description": "x"}
    )
    msg = _msg(subject="invoice", body="x")
    result = _run(email_receipts.extract(msg))
    assert result["status"] == "skipped"
    assert result["reason"] == "missing_or_bad_amount"
    assert expenses.list_recent(10) == []


def test_bad_category_falls_back_to_other(
    tmp_stores, household_sender, stub_anthropic
):
    # A model that invents a category we don't support must not break the
    # add — fall back to 'אחר' so the expense still lands.
    stub_anthropic.create.return_value = _stub_model_response(
        {
            "amount": 42,
            "category": "unknown-made-up-category",
            "payment_method": "not-a-real-method",
            "description": "misc",
        }
    )
    msg = _msg(subject="receipt", body="total 42")
    result = _run(email_receipts.extract(msg))
    assert result["status"] == "added"
    assert result["category"] == "אחר"
    assert result["payment_method"] == "לא צוין"


def test_model_exception_returns_skip(
    tmp_stores, household_sender, stub_anthropic
):
    stub_anthropic.create.side_effect = RuntimeError("network dropped")
    msg = _msg(subject="receipt", body="total 10")
    result = _run(email_receipts.extract(msg))
    assert result["status"] == "skipped"
    assert result["reason"].startswith("model_error")
    assert expenses.list_recent(10) == []


def test_fenced_json_tolerance(tmp_stores, household_sender, stub_anthropic):
    # Some models wrap JSON in ```json ... ``` even when told not to; the
    # extractor must tolerate that rather than skip the whole receipt.
    raw = "```json\n" + json.dumps(
        {
            "amount": 10,
            "category": "אחר",
            "payment_method": "לא צוין",
            "description": "snack",
        },
        ensure_ascii=False,
    ) + "\n```"
    stub_anthropic.create.return_value = _stub_model_response(raw)
    msg = _msg(subject="receipt", body="total 10")
    result = _run(email_receipts.extract(msg))
    assert result["status"] == "added"
    assert result["amount"] == 10


def test_no_household_sender_configured_falls_back(
    tmp_stores, monkeypatch, stub_anthropic
):
    # Dev env with no allowed_sender_numbers — the extractor still saves
    # the row and attributes it to the literal "email".
    monkeypatch.setattr(settings, "allowed_sender_numbers", "")
    stub_anthropic.create.return_value = _stub_model_response(
        {
            "amount": 20,
            "category": "אחר",
            "payment_method": "לא צוין",
            "description": "x",
        }
    )
    msg = _msg(subject="receipt", body="total 20")
    result = _run(email_receipts.extract(msg))
    assert result["status"] == "added"
    stored = expenses.list_recent(1)
    assert stored[0].sender == "email"
