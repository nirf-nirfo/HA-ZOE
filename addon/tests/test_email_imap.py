"""Tests for the IMAP backend.

`imaplib` is mocked at the module level so the tests never try to reach a
real IMAP server. Each test builds a canned RFC822 bytes payload, hands it
to the mock, and verifies the backend's parsing / snippet / attachment
behavior.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from email.message import EmailMessage as StdEmailMessage
from unittest.mock import MagicMock, patch

import pytest

from app import email_imap
from app.email_backend import MAX_ATTACHMENT_BYTES
from app.email_imap import ImapBackend


# ---------------------------------------------------------------- helpers


def _make_conn(search_result: bytes, fetch_bodies: dict[bytes, bytes]):
    """Build a mock IMAP4_SSL instance ready to serve one list/search run.

    search_result: the space-separated uid bytes the server returns.
    fetch_bodies: {uid_bytes: rfc822_bytes}.
    """
    conn = MagicMock()
    conn.login.return_value = ("OK", [b"logged in"])
    conn.select.return_value = ("OK", [b"1"])
    conn.search.return_value = ("OK", [search_result])

    def fetch(uid_bytes, _spec):
        body = fetch_bodies.get(uid_bytes)
        if body is None:
            return ("NO", [])
        # imaplib's real return shape is a list containing a tuple
        # ((envelope-bytes, rfc822-bytes), closing-bytes).
        return ("OK", [(b"1 (RFC822 {len})", body), b")"])

    conn.fetch.side_effect = fetch
    conn.close.return_value = ("OK", [b"closed"])
    conn.logout.return_value = ("BYE", [b"bye"])
    return conn


def _build_rfc822(
    subject: str = "Hello",
    body_plain: str | None = "Hello world body text",
    body_html: str | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
    date_header: str | None = "Mon, 1 Oct 2026 10:00:00 +0000",
    from_addr: str = "sender@example.com",
    to_addr: str = "me@example.com",
) -> bytes:
    msg = StdEmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    if subject is not None:
        msg["Subject"] = subject
    if date_header is not None:
        msg["Date"] = date_header

    if body_plain is not None and body_html is not None:
        # multipart/alternative with plain + html.
        msg.set_content(body_plain)
        msg.add_alternative(body_html, subtype="html")
    elif body_html is not None:
        msg.set_content(body_html, subtype="html")
    elif body_plain is not None:
        msg.set_content(body_plain)
    else:
        msg.set_content("")

    for filename, ctype, content in attachments or []:
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(
            content, maintype=maintype or "application", subtype=subtype or "octet-stream",
            filename=filename,
        )

    return msg.as_bytes()


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def _backend():
    """Backend with non-blank creds so _reject_if_blank passes."""
    return ImapBackend(
        host="imap.test", port=993, address="user@test.com", password="pw"
    )


# -------------------------------------------------------------- unit tests


def test_mask_address_hides_local_part():
    masked = email_imap._mask_address("longuser@example.com")
    assert masked == "lo…@example.com"
    # No address at all.
    assert email_imap._mask_address("") == "(no address)"
    assert email_imap._mask_address("broken") == "(no address)"


def test_blank_credentials_rejected():
    bad = ImapBackend(host="h", port=993, address="", password="")
    with pytest.raises(RuntimeError, match="blank"):
        bad._reject_if_blank()
    bad2 = ImapBackend(host="h", port=993, address="a@b.com", password="")
    with pytest.raises(RuntimeError):
        bad2._reject_if_blank()


def test_list_recent_happy_path(_backend):
    raw = _build_rfc822(subject="Hi there", body_plain="Line one.\nLine two.")
    conn = _make_conn(search_result=b"7", fetch_bodies={b"7": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        results = _run(_backend.list_recent(limit=10, since_hours=24))
    assert len(results) == 1
    msg = results[0]
    assert msg.uid == "7"
    assert msg.subject == "Hi there"
    assert msg.from_addr == "sender@example.com"
    assert "me@example.com" in msg.to_addrs
    # list_recent is snippet-only — no body or attachments.
    assert msg.body_text == ""
    assert msg.attachments == []
    assert "Line one" in msg.snippet


def test_fetch_happy_path(_backend):
    raw = _build_rfc822(
        subject="Receipt",
        body_plain="Total: 42",
        attachments=[("r.pdf", "application/pdf", b"%PDF-small")],
    )
    conn = _make_conn(search_result=b"", fetch_bodies={b"42": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        msg = _run(_backend.fetch("42"))
    assert msg is not None
    assert msg.uid == "42"
    assert msg.subject == "Receipt"
    assert "Total: 42" in msg.body_text
    assert len(msg.attachments) == 1
    att = msg.attachments[0]
    assert att.filename == "r.pdf"
    assert att.content_type == "application/pdf"
    assert att.content == b"%PDF-small"
    assert att.size == len(b"%PDF-small")


def test_search_happy_path(_backend):
    raw1 = _build_rfc822(subject="Match 1", body_plain="hello match")
    raw2 = _build_rfc822(subject="Match 2", body_plain="also match")
    conn = _make_conn(
        search_result=b"1 2", fetch_bodies={b"1": raw1, b"2": raw2}
    )
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        results = _run(_backend.search("match", limit=10))
    assert {m.uid for m in results} == {"1", "2"}
    # Both are snippet-only.
    assert all(not m.body_text for m in results)


def test_search_empty_query_short_circuits(_backend):
    # No IMAP calls when the query is empty.
    with patch.object(email_imap, "imaplib") as mod:
        results = _run(_backend.search("   ", limit=5))
    assert results == []
    assert not mod.IMAP4_SSL.called


def test_html_only_body_strips_tags(_backend):
    raw = _build_rfc822(
        subject="HTML",
        body_plain=None,
        body_html="<p>Hello <b>world</b></p><div>again</div>",
    )
    conn = _make_conn(search_result=b"3", fetch_bodies={b"3": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        msg = _run(_backend.fetch("3"))
    assert msg is not None
    assert "<" not in msg.body_text
    # Expect the visible text (tag content) preserved; exact whitespace is
    # irrelevant.
    collapsed = " ".join(msg.body_text.split())
    assert "Hello" in collapsed and "world" in collapsed and "again" in collapsed


def test_mixed_multipart_prefers_plain(_backend):
    raw = _build_rfc822(
        subject="Mixed",
        body_plain="PLAIN BODY",
        body_html="<p>HTML BODY</p>",
    )
    conn = _make_conn(search_result=b"4", fetch_bodies={b"4": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        msg = _run(_backend.fetch("4"))
    assert msg is not None
    assert "PLAIN BODY" in msg.body_text
    assert "HTML BODY" not in msg.body_text


def test_attachment_under_cap_keeps_content(_backend):
    payload = b"x" * (MAX_ATTACHMENT_BYTES // 2)
    raw = _build_rfc822(
        subject="attach",
        attachments=[("ok.bin", "application/octet-stream", payload)],
    )
    conn = _make_conn(search_result=b"", fetch_bodies={b"5": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        msg = _run(_backend.fetch("5"))
    assert msg is not None
    att = msg.attachments[0]
    assert att.content == payload
    assert att.size == len(payload)


def test_attachment_over_cap_drops_content(_backend):
    payload = b"x" * (MAX_ATTACHMENT_BYTES + 1024)
    raw = _build_rfc822(
        subject="big",
        attachments=[("big.bin", "application/octet-stream", payload)],
    )
    conn = _make_conn(search_result=b"", fetch_bodies={b"6": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        msg = _run(_backend.fetch("6"))
    assert msg is not None
    att = msg.attachments[0]
    assert att.content is None
    # Metadata is kept so the UI/tools can still describe it.
    assert att.filename == "big.bin"
    assert att.content_type == "application/octet-stream"
    assert att.size == len(payload)


def test_snippet_whitespace_collapsed(_backend):
    raw = _build_rfc822(
        subject="s",
        body_plain="   lots   of     whitespace\n\n\nhere\t\tgoes   ",
    )
    conn = _make_conn(search_result=b"9", fetch_bodies={b"9": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        results = _run(_backend.list_recent(limit=5, since_hours=24))
    assert len(results) == 1
    snippet = results[0].snippet
    assert "  " not in snippet
    assert snippet.startswith("lots of whitespace here goes")


def test_missing_headers_tolerated(_backend):
    # Build raw bytes without Subject / Date headers manually.
    raw = (
        b"From: anon@example.com\r\n"
        b"To: me@example.com\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"body here\r\n"
    )
    conn = _make_conn(search_result=b"", fetch_bodies={b"10": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        msg = _run(_backend.fetch("10"))
    assert msg is not None
    assert msg.subject == ""
    # Date falls back to now(UTC) — just verify it's a datetime.
    assert isinstance(msg.date, datetime)
    assert "body here" in msg.body_text


def test_unparseable_date_falls_back(_backend):
    raw = _build_rfc822(
        subject="bad date",
        body_plain="body",
        date_header="not a real date",
    )
    conn = _make_conn(search_result=b"", fetch_bodies={b"11": raw})
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        msg = _run(_backend.fetch("11"))
    assert msg is not None
    assert isinstance(msg.date, datetime)


def test_connection_closed_on_exception(_backend):
    """A fetch-time failure still goes through close + logout.

    Regression guard for the finally block — a leaked socket would mean an
    open IMAP session per failed call, exhausting the server's limit over
    time.
    """
    conn = MagicMock()
    conn.login.return_value = ("OK", [])
    conn.select.return_value = ("OK", [])
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.side_effect = RuntimeError("boom")
    with patch.object(email_imap, "imaplib") as mod:
        mod.IMAP4_SSL.return_value = conn
        with pytest.raises(RuntimeError):
            _run(_backend.list_recent(limit=1, since_hours=24))
    assert conn.logout.called
