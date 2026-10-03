"""Tests for the three email tool handlers.

Mocks `claude_agent._email_backend` directly — the IMAP backend's own
parsing lives in test_email_imap.py; here we only care about the dict
shape surfaced to the model and the "not configured" short-circuit.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app import claude_agent
from app.email_backend import EmailAttachment, EmailMessage
from app.handlers.email import _handle_email_call
from app.settings import settings


def _run(coro):
    return asyncio.run(coro)


def _canned_message(uid: str = "1", body: str = "hello world") -> EmailMessage:
    return EmailMessage(
        uid=uid,
        from_addr="alice@example.com",
        to_addrs=["me@example.com"],
        subject="Hi",
        date=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        snippet=body[:200],
        body_text=body,
        attachments=[
            EmailAttachment(
                filename="r.pdf",
                content_type="application/pdf",
                size=42,
                content=b"%PDF",
            ),
        ],
    )


@pytest.fixture
def configured_email(monkeypatch):
    monkeypatch.setattr(settings, "email_address", "me@example.com")
    monkeypatch.setattr(settings, "email_password", "pw")
    yield


@pytest.fixture
def blank_email(monkeypatch):
    monkeypatch.setattr(settings, "email_address", "")
    monkeypatch.setattr(settings, "email_password", "")
    yield


# ------------------------------------------------- configured-happy-path tests


def test_list_recent_emails_handler_shape(monkeypatch, configured_email):
    msg = _canned_message(uid="7")
    backend = AsyncMock()
    backend.list_recent.return_value = [msg]
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    out = _run(claude_agent.handle_list_recent_emails(limit=5, since_hours=12))
    assert "error" not in out
    assert len(out["emails"]) == 1
    row = out["emails"][0]
    assert row["uid"] == "7"
    assert row["from"] == "alice@example.com"
    assert row["subject"] == "Hi"
    assert row["snippet"] == "hello world"
    assert row["date"].startswith("2026-10-01T")
    backend.list_recent.assert_awaited_once_with(limit=5, since_hours=12)


def test_read_email_handler_shape(monkeypatch, configured_email):
    msg = _canned_message(uid="9", body="full body")
    backend = AsyncMock()
    backend.fetch.return_value = msg
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    out = _run(claude_agent.handle_read_email(uid="9"))
    assert "error" not in out
    assert out["uid"] == "9"
    assert out["body_text"] == "full body"
    # Attachment metadata is included but raw bytes are NOT.
    assert len(out["attachments"]) == 1
    att = out["attachments"][0]
    assert att["filename"] == "r.pdf"
    assert att["content_type"] == "application/pdf"
    assert att["size"] == 42
    assert "content" not in att
    backend.fetch.assert_awaited_once_with("9")


def test_read_email_missing_message(monkeypatch, configured_email):
    backend = AsyncMock()
    backend.fetch.return_value = None
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    out = _run(claude_agent.handle_read_email(uid="nope"))
    assert out == {"error": "message not found"}


def test_search_emails_handler_shape(monkeypatch, configured_email):
    msg = _canned_message(uid="2")
    backend = AsyncMock()
    backend.search.return_value = [msg]
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    out = _run(claude_agent.handle_search_emails(query="hi", limit=3))
    assert "error" not in out
    assert out["emails"][0]["uid"] == "2"
    backend.search.assert_awaited_once_with("hi", limit=3)


# -------------------------------------------------------- not-configured guard


def test_list_recent_emails_blank_credentials(monkeypatch, blank_email):
    # Backend MUST NOT be touched when creds are blank.
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(claude_agent.handle_list_recent_emails())
    assert out["error"].startswith("email not configured")
    backend.list_recent.assert_not_awaited()


def test_read_email_blank_credentials(monkeypatch, blank_email):
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(claude_agent.handle_read_email(uid="1"))
    assert out["error"].startswith("email not configured")
    backend.fetch.assert_not_awaited()


def test_search_emails_blank_credentials(monkeypatch, blank_email):
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(claude_agent.handle_search_emails(query="x"))
    assert out["error"].startswith("email not configured")
    backend.search.assert_not_awaited()


def test_password_only_set_still_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "email_address", "")
    monkeypatch.setattr(settings, "email_password", "pw")
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(claude_agent.handle_list_recent_emails())
    assert out["error"].startswith("email not configured")


# -------------------------------------------- dispatcher JSON wrapping


def test_dispatcher_wraps_list_recent_as_json(monkeypatch, configured_email):
    msg = _canned_message(uid="D1")
    backend = AsyncMock()
    backend.list_recent.return_value = [msg]
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    out = _run(
        _handle_email_call("sender1", claude_agent._LIST_RECENT_EMAILS, {
            "limit": "4",       # string that must be coerced
            "since_hours": None,
        })
    )
    data = json.loads(out)
    assert data["emails"][0]["uid"] == "D1"
    backend.list_recent.assert_awaited_once_with(limit=4, since_hours=24)


def test_dispatcher_requires_uid(monkeypatch, configured_email):
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(_handle_email_call("sender1", claude_agent._READ_EMAIL, {}))
    assert json.loads(out) == {"error": "uid is required"}
    backend.fetch.assert_not_awaited()


def test_dispatcher_requires_query(monkeypatch, configured_email):
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(_handle_email_call("sender1", claude_agent._SEARCH_EMAILS, {"query": "   "}))
    assert json.loads(out) == {"error": "query is required"}
    backend.search.assert_not_awaited()


def test_dispatcher_unknown_tool_returns_empty():
    out = _run(_handle_email_call("s", "not-a-tool", {}))
    assert out == ""


# ------------------------------------------- Item 20: process_email_now tool


def test_process_email_now_requires_uid(monkeypatch, configured_email):
    # Mirrors the uid guard the other two write-shaped email tools use —
    # dispatcher refuses an empty uid before touching the backend.
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(_handle_email_call("s", claude_agent._PROCESS_EMAIL_NOW, {"uid": ""}))
    assert json.loads(out) == {"error": "uid is required"}
    backend.fetch.assert_not_awaited()


def test_process_email_now_blank_credentials(monkeypatch, blank_email):
    backend = AsyncMock()
    monkeypatch.setattr(claude_agent, "_email_backend", backend)
    out = _run(claude_agent.handle_process_email_now(uid="1"))
    assert out["error"].startswith("email not configured")
    backend.fetch.assert_not_awaited()


def test_process_email_now_missing_message(monkeypatch, configured_email):
    backend = AsyncMock()
    backend.fetch.return_value = None
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    out = _run(claude_agent.handle_process_email_now(uid="nope"))
    assert out == {"error": "message not found"}


def test_process_email_now_delegates_to_processor_with_force(
    monkeypatch, configured_email
):
    # The manual tool MUST pass force=True so it works regardless of the
    # email_auto_extract setting.
    msg = _canned_message(uid="42")
    backend = AsyncMock()
    backend.fetch.return_value = msg
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    from app import email_processor
    fake_process = AsyncMock(
        return_value={"status": "processed", "receipt": {"status": "not_receipt"},
                      "ical": {"status": "no_events"}}
    )
    monkeypatch.setattr(email_processor, "process_email", fake_process)

    out = _run(claude_agent.handle_process_email_now(uid="42"))
    assert out["status"] == "processed"
    backend.fetch.assert_awaited_once_with("42")
    fake_process.assert_awaited_once()
    assert fake_process.await_args.kwargs.get("force") is True


def test_dispatcher_wraps_process_email_now(monkeypatch, configured_email):
    msg = _canned_message(uid="d-42")
    backend = AsyncMock()
    backend.fetch.return_value = msg
    monkeypatch.setattr(claude_agent, "_email_backend", backend)

    from app import email_processor
    monkeypatch.setattr(
        email_processor,
        "process_email",
        AsyncMock(return_value={"status": "processed",
                                "receipt": {"status": "not_receipt"},
                                "ical": {"status": "no_events"}}),
    )

    out = _run(
        _handle_email_call("s", claude_agent._PROCESS_EMAIL_NOW, {"uid": "d-42"})
    )
    data = json.loads(out)
    assert data["status"] == "processed"
