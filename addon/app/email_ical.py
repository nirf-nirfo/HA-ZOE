"""Item 20: extract calendar invites from an email → agenda rows.

Shape:
  extract(msg) -> {"status": "added", "events_added": N, "events_skipped": M,
                   "events": [{"date": ..., "text": ...}, ...]}
                | {"status": "no_events"}

Flow:
  1. Walk attachments looking for anything whose content_type is text/calendar
     or whose filename ends with .ics. If none, scan the body for an inline
     VCALENDAR block.
  2. Parse each VEVENT with `icalendar`. Pull SUMMARY, DTSTART, DTEND,
     LOCATION, DESCRIPTION.
  3. For each event:
       - Compute an ISO YYYY-MM-DD date from DTSTART (date-only VALUE=DATE
         or datetime).
       - Compose a display text: "<SUMMARY> — <LOCATION>" (or just SUMMARY).
       - De-duplicate against `agenda.items_for_date(sender, date)` by exact
         text match. The watch loop (Item 21) will re-see the same invite
         on every tick; the dedup prevents the agenda from growing a copy
         per hour.
       - Call `agenda.add_item(sender, date, text)`.

The function is sync — iCal parsing is CPU-bound and tiny. `email_processor`
wraps the call in `asyncio.to_thread` to keep the Item 21 watch loop async.
Nothing here raises on malformed input: an unparseable DTSTART, a missing
SUMMARY, a VEVENT without any date → counted in `events_skipped`.
"""

from __future__ import annotations

import logging
import re
from datetime import date as date_cls, datetime
from typing import Any

from icalendar import Calendar

from app import agenda
from app.email_backend import EmailMessage
from app.settings import settings

logger = logging.getLogger(__name__)


# Captures BEGIN:VCALENDAR...END:VCALENDAR anywhere in a body (plain or
# the stripped-HTML form). Non-greedy so a mail with multiple blocks is
# split into separate parses. DOTALL so .*? crosses newlines.
_VCAL_RE = re.compile(
    r"BEGIN:VCALENDAR.*?END:VCALENDAR", re.DOTALL | re.IGNORECASE
)


def _primary_household_sender() -> str:
    """Email → agenda rows attribute to the first household sender.

    Agenda is per-sender, so events have to land on *someone*; the primary
    sender is the household's "default inbox owner". Item 21 only notifies
    this sender when iCal events are added — the other sender's agenda is
    not written to from email.
    """
    numbers = [
        n.strip() for n in (settings.allowed_sender_numbers or "").split(",") if n.strip()
    ]
    return numbers[0] if numbers else "email"


def _ics_sources(msg: EmailMessage) -> list[bytes]:
    """Returns candidate ICS payloads from the message, as bytes.

    Order: attachments first (strongest signal), then any inline VCALENDAR
    found in the body. Duplicates are allowed — each payload is parsed
    independently and the per-event dedup catches repeats.
    """
    sources: list[bytes] = []
    for att in msg.attachments:
        ctype = (att.content_type or "").lower()
        name = (att.filename or "").lower()
        if ctype == "text/calendar" or name.endswith(".ics"):
            if att.content:
                sources.append(att.content)
            # content=None (stripped by MAX_ATTACHMENT_BYTES) → cannot
            # recover; skip. iCal invites are tiny in practice.
    body = msg.body_text or ""
    for match in _VCAL_RE.finditer(body):
        sources.append(match.group(0).encode("utf-8", errors="replace"))
    return sources


def _event_date(dtstart: Any) -> str | None:
    """Normalise an icalendar DTSTART value to ISO YYYY-MM-DD.

    DTSTART can be a `date` (all-day event) or a `datetime` (timed event).
    Timed events may be tz-aware or naive; either way we only need the
    date part for an agenda row.
    """
    if isinstance(dtstart, datetime):
        return dtstart.strftime("%Y-%m-%d")
    if isinstance(dtstart, date_cls):
        return dtstart.strftime("%Y-%m-%d")
    return None


def _event_text(summary: str, location: str) -> str:
    summary = (summary or "").strip()
    location = (location or "").strip()
    if summary and location:
        return f"{summary} — {location}"
    return summary or location or "(event)"


def _parse_one(ics_bytes: bytes) -> list[tuple[str, str]]:
    """Parses one VCALENDAR payload → list of (date_iso, text) tuples.

    A malformed payload is logged and silently drops to an empty list so
    one bad invite can't knock out the rest of the batch.
    """
    try:
        cal = Calendar.from_ical(ics_bytes)
    except Exception:
        logger.exception("email_ical: failed to parse VCALENDAR payload")
        return []

    out: list[tuple[str, str]] = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        dtstart = comp.get("DTSTART")
        if dtstart is None:
            continue
        try:
            date_iso = _event_date(dtstart.dt)
        except Exception:
            logger.exception("email_ical: VEVENT with unexpected DTSTART shape")
            continue
        if not date_iso:
            continue
        summary = str(comp.get("SUMMARY") or "")
        location = str(comp.get("LOCATION") or "")
        out.append((date_iso, _event_text(summary, location)))
    return out


def extract(msg: EmailMessage) -> dict[str, Any]:
    """iCal attachments / inline VCALENDAR → zero-or-more agenda rows.

    See module docstring for return shape. Never raises.
    """
    sources = _ics_sources(msg)
    if not sources:
        return {"status": "no_events"}

    sender = _primary_household_sender()
    added: list[dict[str, str]] = []
    skipped = 0

    for payload in sources:
        for date_iso, text in _parse_one(payload):
            # Dedup against existing agenda rows for the same day. An
            # exact text match is enough — Item 21 will re-process the
            # same invite each hour, and this check keeps the agenda
            # from growing a per-hour copy.
            try:
                existing = agenda.items_for_date(sender, date_iso)
            except Exception:
                logger.exception("email_ical: items_for_date failed")
                skipped += 1
                continue
            if any(i.text == text for i in existing):
                skipped += 1
                continue
            try:
                item = agenda.add_item(sender, date_iso, text)
            except Exception:
                logger.exception("email_ical: agenda.add_item failed")
                skipped += 1
                continue
            added.append({"id": item.id, "date": date_iso, "text": text})

    if not added and skipped == 0:
        # Payload(s) parsed but held no VEVENTs — treat like no_events so
        # the caller doesn't notify about an invite that wasn't there.
        return {"status": "no_events"}

    logger.info(
        "email_ical: added=%d skipped=%d events for email uid=%s",
        len(added),
        skipped,
        msg.uid,
    )
    return {
        "status": "added" if added else "no_events",
        "events_added": len(added),
        "events_skipped": skipped,
        "events": added,
    }
