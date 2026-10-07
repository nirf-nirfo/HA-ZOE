"""Item 23: cross-sender briefing management.

Covers the three new tools wired into handlers/agenda.py:
list_household_members, copy_my_briefing_to, set_briefing_for.

The household-membership guard (target must be in allowed_sender_numbers)
is the main thing to pin down — the briefing tools must never be able to
mutate a config for an outside phone the model happens to invent.
"""
from unittest.mock import patch

import pytest

from app import briefing, memory
from app.handlers.agenda import _handle_agenda_call
from app.settings import settings

NIR = "972501111111"
WIFE = "972502222222"
OUTSIDER = "972509999999"


@pytest.fixture(autouse=True)
def _household(tmp_stores, monkeypatch):
    """Two-member household; refresh the senders cache so memory facts take."""
    monkeypatch.setattr(settings, "allowed_sender_numbers", f"{NIR},{WIFE}")
    from app import senders
    senders.refresh()
    yield
    senders.refresh()


def _seed_memory_names():
    memory.remember(f"the number {NIR} is Nir")
    memory.remember(f"the number {WIFE} is Shira")
    from app import senders
    senders.refresh()


def test_list_household_members_resolves_names():
    _seed_memory_names()
    out = _handle_agenda_call(NIR, "list_household_members", {})
    assert "Nir" in out and NIR in out
    assert "Shira" in out and WIFE in out


def test_list_household_members_falls_back_to_phone_when_no_memory():
    out = _handle_agenda_call(NIR, "list_household_members", {})
    # No memory facts seeded → resolve() returns the phone itself.
    assert NIR in out and WIFE in out


def test_copy_my_briefing_to_household_member():
    _seed_memory_names()
    briefing.set_config(NIR, 7, 30, enabled=True)
    briefing.set_evening_config(NIR, 21, 15, enabled=True)

    out = _handle_agenda_call(NIR, "copy_my_briefing_to", {"target_sender": WIFE})

    assert "Copied" in out and "Shira" in out
    wife = briefing.get_config(WIFE)
    assert wife is not None
    assert (wife.hour, wife.minute, wife.enabled) == (7, 30, True)
    assert (wife.evening_hour, wife.evening_minute, wife.evening_enabled) == (21, 15, True)


def test_copy_preserves_targets_already_sent_dedup_state():
    """A copy mid-day must not resend today's brief to the target."""
    _seed_memory_names()
    briefing.set_config(NIR, 7, 30)
    briefing.set_evening_config(NIR, 21, 15)
    briefing.set_config(WIFE, 8, 0)
    briefing.mark_sent(WIFE, "2026-10-07")
    briefing.mark_evening_sent(WIFE, "2026-10-07")

    _handle_agenda_call(NIR, "copy_my_briefing_to", {"target_sender": WIFE})

    wife = briefing.get_config(WIFE)
    assert wife.last_sent_date == "2026-10-07"
    assert wife.last_evening_sent_date == "2026-10-07"


def test_copy_rejects_outsider():
    briefing.set_config(NIR, 7, 30)
    out = _handle_agenda_call(NIR, "copy_my_briefing_to", {"target_sender": OUTSIDER})
    assert "not a household member" in out
    assert briefing.get_config(OUTSIDER) is None


def test_copy_rejects_self():
    briefing.set_config(NIR, 7, 30)
    out = _handle_agenda_call(NIR, "copy_my_briefing_to", {"target_sender": NIR})
    assert "That's you" in out


def test_copy_requires_caller_has_config():
    out = _handle_agenda_call(NIR, "copy_my_briefing_to", {"target_sender": WIFE})
    assert "don't have a briefing config" in out
    assert briefing.get_config(WIFE) is None


def test_set_briefing_for_morning():
    _seed_memory_names()
    out = _handle_agenda_call(
        NIR, "set_briefing_for",
        {"target_sender": WIFE, "kind": "morning", "hour": 6, "minute": 45},
    )
    assert "Shira" in out and "06:45" in out
    wife = briefing.get_config(WIFE)
    assert (wife.hour, wife.minute, wife.enabled) == (6, 45, True)


def test_set_briefing_for_evening_disabled():
    _seed_memory_names()
    out = _handle_agenda_call(
        NIR, "set_briefing_for",
        {"target_sender": WIFE, "kind": "evening", "hour": 20, "minute": 0, "enabled": False},
    )
    assert "turned off" in out
    wife = briefing.get_config(WIFE)
    assert wife.evening_enabled is False


def test_set_briefing_for_rejects_outsider():
    out = _handle_agenda_call(
        NIR, "set_briefing_for",
        {"target_sender": OUTSIDER, "kind": "morning", "hour": 7, "minute": 0},
    )
    assert "not a household member" in out
    assert briefing.get_config(OUTSIDER) is None


def test_set_briefing_for_rejects_bad_kind():
    out = _handle_agenda_call(
        NIR, "set_briefing_for",
        {"target_sender": WIFE, "kind": "midnight", "hour": 0, "minute": 0},
    )
    assert "'morning' or 'evening'" in out


def test_set_briefing_for_rejects_bad_time():
    out = _handle_agenda_call(
        NIR, "set_briefing_for",
        {"target_sender": WIFE, "kind": "morning", "hour": 25, "minute": 0},
    )
    assert "0-23" in out


def test_set_briefing_for_missing_target():
    out = _handle_agenda_call(
        NIR, "set_briefing_for",
        {"kind": "morning", "hour": 7, "minute": 0},
    )
    assert "target phone" in out
