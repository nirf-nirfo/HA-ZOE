"""Tests for the Item 20 orchestrator.

The two extractors are mocked at module level so these tests exercise the
orchestration surface only — the flag gate, the per-extractor error
containment, the broadcast on receipt-add, and the per-sender
notification on iCal-add. Extractor-level behaviour is covered by
test_email_receipts.py and test_email_ical.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import email_processor
from app.email_backend import EmailMessage
from app.settings import settings


def _run(coro):
    return asyncio.run(coro)


def _msg(uid: str = "u1") -> EmailMessage:
    return EmailMessage(
        uid=uid,
        from_addr="shop@example.com",
        to_addrs=["me@example.com"],
        subject="receipt",
        date=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        snippet="",
        body_text="",
        attachments=[],
    )


@pytest.fixture
def household(monkeypatch):
    monkeypatch.setattr(
        settings, "allowed_sender_numbers", "972501234567,972509999999"
    )
    yield ("972501234567", "972509999999")


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(settings, "email_auto_extract", False)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "email_auto_extract", True)


# ------------------------------------------------- flag gate


def test_flag_off_short_circuits(household, flag_off, monkeypatch):
    receipt_mock = AsyncMock()
    monkeypatch.setattr(email_processor.email_receipts, "extract", receipt_mock)
    ical_mock = MagicMock()
    monkeypatch.setattr(email_processor.email_ical, "extract", ical_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr(email_processor, "send_message", send_mock)

    result = _run(email_processor.process_email(_msg()))

    assert result == {"status": "disabled"}
    receipt_mock.assert_not_awaited()
    ical_mock.assert_not_called()
    send_mock.assert_not_awaited()


def test_force_bypasses_flag(household, flag_off, monkeypatch):
    # The manual process_email_now tool calls through with force=True —
    # that path must run the extractors even when the add-on flag is OFF.
    receipt_mock = AsyncMock(return_value={"status": "not_receipt", "reason": "no_keyword"})
    monkeypatch.setattr(email_processor.email_receipts, "extract", receipt_mock)
    ical_mock = MagicMock(return_value={"status": "no_events"})
    monkeypatch.setattr(email_processor.email_ical, "extract", ical_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr(email_processor, "send_message", send_mock)

    result = _run(email_processor.process_email(_msg(), force=True))

    assert result["status"] == "processed"
    receipt_mock.assert_awaited_once()
    ical_mock.assert_called_once()


# --------------------------------------------- flag-on: both extractors run


def test_flag_on_runs_both_extractors(household, flag_on, monkeypatch):
    receipt_mock = AsyncMock(return_value={"status": "not_receipt", "reason": "no_keyword"})
    monkeypatch.setattr(email_processor.email_receipts, "extract", receipt_mock)
    ical_mock = MagicMock(return_value={"status": "no_events"})
    monkeypatch.setattr(email_processor.email_ical, "extract", ical_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr(email_processor, "send_message", send_mock)

    result = _run(email_processor.process_email(_msg()))

    assert result["status"] == "processed"
    assert result["receipt"]["status"] == "not_receipt"
    assert result["ical"]["status"] == "no_events"
    receipt_mock.assert_awaited_once()
    ical_mock.assert_called_once()
    # Neither extractor added anything, so no broadcast / notification.
    send_mock.assert_not_awaited()


# ---------------------------------------------- side effects on add


def test_receipt_add_triggers_household_broadcast(household, flag_on, monkeypatch):
    primary, secondary = household
    receipt_mock = AsyncMock(
        return_value={
            "status": "added",
            "expense_id": "abc123",
            "amount": 150,
            "category": "סופר",
            "payment_method": "MAX",
            "description": "קניות",
            "date": "2026-10-01",
        }
    )
    monkeypatch.setattr(email_processor.email_receipts, "extract", receipt_mock)
    monkeypatch.setattr(
        email_processor.email_ical, "extract", MagicMock(return_value={"status": "no_events"})
    )
    send_mock = AsyncMock()
    monkeypatch.setattr(email_processor, "send_message", send_mock)

    result = _run(email_processor.process_email(_msg()))

    assert result["status"] == "processed"
    assert result["receipt"]["status"] == "added"

    # Broadcast lands on BOTH senders — expense tool outcomes are
    # household-wide (see CLAUDE.md's BROADCAST SEMANTICS block).
    sent_to = {call.args[0] for call in send_mock.await_args_list}
    assert sent_to == {primary, secondary}
    # The broadcast text mirrors the add_expense handler's reply shape so
    # the WhatsApp message reads the same whether an expense came in by
    # photo or by email.
    for call in send_mock.await_args_list:
        text = call.args[1]
        assert "150" in text
        assert "סופר" in text
        assert "MAX" in text
        assert "abc123" in text


def test_ical_add_notifies_only_primary(household, flag_on, monkeypatch):
    primary, secondary = household
    monkeypatch.setattr(
        email_processor.email_receipts,
        "extract",
        AsyncMock(return_value={"status": "not_receipt", "reason": "no_keyword"}),
    )
    ical_mock = MagicMock(
        return_value={
            "status": "added",
            "events_added": 2,
            "events_skipped": 0,
            "events": [
                {"id": "a", "date": "2026-10-15", "text": "Dentist"},
                {"id": "b", "date": "2026-10-20", "text": "School trip"},
            ],
        }
    )
    monkeypatch.setattr(email_processor.email_ical, "extract", ical_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr(email_processor, "send_message", send_mock)

    result = _run(email_processor.process_email(_msg()))

    assert result["ical"]["events_added"] == 2

    # Exactly one message, to the primary — never DM the wife (CLAUDE.md
    # Meta 24h-window rule).
    assert send_mock.await_count == 1
    call = send_mock.await_args_list[0]
    assert call.args[0] == primary
    assert call.args[0] != secondary
    assert "Dentist" in call.args[1]
    assert "School trip" in call.args[1]


# ---------------------------------------------- error containment


def test_receipt_crash_still_runs_ical(household, flag_on, monkeypatch):
    # One extractor crashing must NOT stop the other — Item 21's watch loop
    # processes each email once per tick; a crash here would orphan the
    # iCal event on that invite.
    monkeypatch.setattr(
        email_processor.email_receipts,
        "extract",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    ical_mock = MagicMock(return_value={"status": "no_events"})
    monkeypatch.setattr(email_processor.email_ical, "extract", ical_mock)
    monkeypatch.setattr(email_processor, "send_message", AsyncMock())

    result = _run(email_processor.process_email(_msg()))

    assert result["status"] == "processed"
    assert result["receipt"]["status"] == "skipped"
    assert "crash" in result["receipt"]["reason"]
    ical_mock.assert_called_once()


def test_ical_crash_still_reports_receipt(household, flag_on, monkeypatch):
    monkeypatch.setattr(
        email_processor.email_receipts,
        "extract",
        AsyncMock(return_value={"status": "not_receipt", "reason": "no_keyword"}),
    )
    monkeypatch.setattr(
        email_processor.email_ical,
        "extract",
        MagicMock(side_effect=RuntimeError("ical blew up")),
    )
    monkeypatch.setattr(email_processor, "send_message", AsyncMock())

    result = _run(email_processor.process_email(_msg()))

    assert result["status"] == "processed"
    assert result["ical"]["status"] == "skipped"
    assert "crash" in result["ical"]["reason"]


def test_broadcast_send_failure_does_not_propagate(household, flag_on, monkeypatch):
    # A closed 24h Meta window makes `send_message` raise. The processor
    # must swallow that — otherwise one closed window would stop the other
    # recipient from getting the broadcast too, and later would stop the
    # watch loop from marking the email as processed.
    monkeypatch.setattr(
        email_processor.email_receipts,
        "extract",
        AsyncMock(
            return_value={
                "status": "added",
                "expense_id": "x",
                "amount": 10,
                "category": "אחר",
                "payment_method": "לא צוין",
                "description": "x",
                "date": "2026-10-01",
            }
        ),
    )
    monkeypatch.setattr(
        email_processor.email_ical, "extract", MagicMock(return_value={"status": "no_events"})
    )
    send_mock = AsyncMock(side_effect=RuntimeError("24h window closed"))
    monkeypatch.setattr(email_processor, "send_message", send_mock)

    # Should NOT raise.
    result = _run(email_processor.process_email(_msg()))
    assert result["receipt"]["status"] == "added"
    # Both recipients were attempted.
    assert send_mock.await_count == 2


def test_no_primary_sender_skips_ical_notification(flag_on, monkeypatch):
    monkeypatch.setattr(settings, "allowed_sender_numbers", "")
    monkeypatch.setattr(
        email_processor.email_receipts,
        "extract",
        AsyncMock(return_value={"status": "not_receipt", "reason": "no_keyword"}),
    )
    monkeypatch.setattr(
        email_processor.email_ical,
        "extract",
        MagicMock(
            return_value={
                "status": "added",
                "events_added": 1,
                "events_skipped": 0,
                "events": [{"id": "a", "date": "2026-11-01", "text": "x"}],
            }
        ),
    )
    send_mock = AsyncMock()
    monkeypatch.setattr(email_processor, "send_message", send_mock)

    result = _run(email_processor.process_email(_msg()))
    assert result["ical"]["events_added"] == 1
    # Nothing to notify — no configured number.
    send_mock.assert_not_awaited()
