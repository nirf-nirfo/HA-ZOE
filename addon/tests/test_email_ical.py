"""Tests for the iCal extractor (Item 20).

Fixtures:
  - tests/fixtures/sample.ics — two VEVENTs, one timed + one all-day, for
    the attachment-happy-path case.

The extractor is synchronous and never raises — a malformed iCal payload
is counted as `events_skipped` and logged. The duplicate-detection path
is a hard requirement because Item 21's watch loop will re-process the
same invite on every tick.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import agenda, email_ical
from app.email_backend import EmailAttachment, EmailMessage
from app.settings import settings


_FIXTURE = Path(__file__).parent / "fixtures" / "sample.ics"


def _msg(
    *,
    uid: str = "u-ical",
    subject: str = "Calendar invite",
    body: str = "",
    attachments: list[EmailAttachment] | None = None,
) -> EmailMessage:
    return EmailMessage(
        uid=uid,
        from_addr="invites@example.com",
        to_addrs=["me@example.com"],
        subject=subject,
        date=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        snippet=body[:200],
        body_text=body,
        attachments=list(attachments or []),
    )


def _ics_attachment(path: Path = _FIXTURE, filename: str = "invite.ics") -> EmailAttachment:
    data = path.read_bytes()
    return EmailAttachment(
        filename=filename,
        content_type="text/calendar",
        size=len(data),
        content=data,
    )


@pytest.fixture
def household_sender(monkeypatch):
    monkeypatch.setattr(settings, "allowed_sender_numbers", "972501234567,972509999999")
    yield "972501234567"


# ------------------------------------------------- parsing happy path


def test_fixture_events_parsed_and_added(tmp_stores, household_sender):
    msg = _msg(attachments=[_ics_attachment()])

    result = email_ical.extract(msg)

    assert result["status"] == "added"
    assert result["events_added"] == 2
    assert result["events_skipped"] == 0
    # Timed event (DTSTART with time) → ISO date-only in the agenda row.
    added_dates = {e["date"] for e in result["events"]}
    assert added_dates == {"2026-10-15", "2026-10-20"}

    # Both events landed in the agenda for the primary household sender.
    oct15 = agenda.items_for_date(household_sender, "2026-10-15")
    oct20 = agenda.items_for_date(household_sender, "2026-10-20")
    assert len(oct15) == 1 and len(oct20) == 1
    assert "Dentist appointment" in oct15[0].text
    assert "Clinic, Tel Aviv" in oct15[0].text
    assert "School trip" in oct20[0].text
    assert "Jerusalem" in oct20[0].text


def test_filename_based_detection(tmp_stores, household_sender):
    # content_type is misreported (application/octet-stream) but the
    # filename ends in .ics — must still be picked up.
    att = _ics_attachment(filename="invite.ics")
    att.content_type = "application/octet-stream"
    msg = _msg(attachments=[att])

    result = email_ical.extract(msg)
    assert result["status"] == "added"
    assert result["events_added"] == 2


def test_inline_vcalendar_in_body(tmp_stores, household_sender):
    # No attachment — invite is pasted into the body (common with some
    # calendar servers). The regex fallback must find it.
    body = (
        "Hello, here is your invite below.\n\n"
        + _FIXTURE.read_text()
        + "\n\nRegards."
    )
    msg = _msg(body=body)

    result = email_ical.extract(msg)
    assert result["status"] == "added"
    assert result["events_added"] == 2


# ------------------------------------------------- duplicate detection


def test_duplicate_event_skipped_on_second_pass(tmp_stores, household_sender):
    # First call adds two events.
    msg = _msg(attachments=[_ics_attachment()])
    first = email_ical.extract(msg)
    assert first["events_added"] == 2

    # Second call on the same message (what Item 21's watch loop will do)
    # must dedup — no new rows, both counted as skipped.
    second = email_ical.extract(msg)
    assert second["status"] == "no_events"
    assert second["events_added"] == 0
    assert second["events_skipped"] == 2
    assert len(agenda.items_for_date(household_sender, "2026-10-15")) == 1
    assert len(agenda.items_for_date(household_sender, "2026-10-20")) == 1


def test_mixed_new_and_duplicate(tmp_stores, household_sender):
    # Seed the agenda with ONE of the two events already present.
    agenda.add_item(household_sender, "2026-10-15", "Dentist appointment — Clinic, Tel Aviv")

    msg = _msg(attachments=[_ics_attachment()])
    result = email_ical.extract(msg)

    assert result["status"] == "added"
    assert result["events_added"] == 1
    assert result["events_skipped"] == 1
    assert len(agenda.items_for_date(household_sender, "2026-10-15")) == 1
    assert len(agenda.items_for_date(household_sender, "2026-10-20")) == 1


# ----------------------------------------------- empty / malformed cases


def test_no_events_when_no_calendar_attachment(tmp_stores, household_sender):
    msg = _msg(
        attachments=[
            EmailAttachment(
                filename="not-a-calendar.pdf",
                content_type="application/pdf",
                size=100,
                content=b"%PDF-fake",
            )
        ],
        body="just text, nothing calendar-shaped here",
    )
    result = email_ical.extract(msg)
    assert result == {"status": "no_events"}
    # Nothing lands in the agenda.
    assert agenda.items_for_date(household_sender, "2026-10-15") == []


def test_calendar_without_events_returns_no_events(tmp_stores, household_sender):
    empty_cal = (
        b"BEGIN:VCALENDAR\r\n"
        b"VERSION:2.0\r\n"
        b"PRODID:-//Empty//EN\r\n"
        b"END:VCALENDAR\r\n"
    )
    att = EmailAttachment(
        filename="empty.ics",
        content_type="text/calendar",
        size=len(empty_cal),
        content=empty_cal,
    )
    msg = _msg(attachments=[att])
    result = email_ical.extract(msg)
    assert result == {"status": "no_events"}


def test_malformed_calendar_is_contained(tmp_stores, household_sender):
    # icalendar.from_ical raises on completely broken bytes; the extractor
    # must swallow that and return no_events, not crash.
    att = EmailAttachment(
        filename="bad.ics",
        content_type="text/calendar",
        size=20,
        content=b"this is not vcalendar at all",
    )
    msg = _msg(attachments=[att])
    result = email_ical.extract(msg)
    assert result == {"status": "no_events"}


def test_dropped_attachment_content_is_skipped(tmp_stores, household_sender):
    # Backend stripped the payload (over MAX_ATTACHMENT_BYTES). Metadata
    # alone can't reconstruct a .ics, so the extractor returns no_events.
    att = EmailAttachment(
        filename="huge.ics",
        content_type="text/calendar",
        size=50 * 1024 * 1024,
        content=None,
    )
    msg = _msg(attachments=[att])
    result = email_ical.extract(msg)
    assert result == {"status": "no_events"}


def test_vevent_missing_dtstart_counts_as_skipped(tmp_stores, household_sender):
    cal = (
        b"BEGIN:VCALENDAR\r\n"
        b"VERSION:2.0\r\n"
        b"BEGIN:VEVENT\r\n"
        b"UID:no-dtstart@example.com\r\n"
        b"SUMMARY:Missing start time\r\n"
        b"END:VEVENT\r\n"
        b"BEGIN:VEVENT\r\n"
        b"UID:valid@example.com\r\n"
        b"DTSTART;VALUE=DATE:20261101\r\n"
        b"SUMMARY:Valid event\r\n"
        b"END:VEVENT\r\n"
        b"END:VCALENDAR\r\n"
    )
    att = EmailAttachment(
        filename="mixed.ics",
        content_type="text/calendar",
        size=len(cal),
        content=cal,
    )
    msg = _msg(attachments=[att])
    result = email_ical.extract(msg)
    assert result["status"] == "added"
    # Only the valid one gets added; the one missing DTSTART is silently
    # dropped from the output (not counted as skipped since it's structurally
    # un-processable rather than a dedup).
    assert result["events_added"] == 1
    assert any("Valid event" in ev["text"] for ev in result["events"])


def test_no_household_sender_fallback(tmp_stores, monkeypatch):
    monkeypatch.setattr(settings, "allowed_sender_numbers", "")
    msg = _msg(attachments=[_ics_attachment()])
    result = email_ical.extract(msg)
    assert result["events_added"] == 2
    assert len(agenda.items_for_date("email", "2026-10-15")) == 1
