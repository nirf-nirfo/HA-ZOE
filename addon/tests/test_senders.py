"""Tests for the phone -> name resolver (Item 13).

Uses `tmp_stores` so memory.all_facts() reads from an isolated temp file.
Each test calls senders.refresh() before resolving so the module-level cache
doesn't leak state from prior tests in the same process.
"""

import pytest

from app import memory, senders


@pytest.fixture(autouse=True)
def _reset_cache():
    senders.refresh()
    yield
    senders.refresh()


def test_empty_memory_returns_phone_unchanged(tmp_stores):
    assert senders.resolve("+972501234567") == "+972501234567"


def test_hebrew_hu_shel(tmp_stores):
    memory.remember("המספר 972501234567 הוא של ניר")
    assert senders.resolve("+972501234567") == "ניר"


def test_hebrew_shayakh_le(tmp_stores):
    memory.remember("המספר +972501234567 שייך לניר")
    assert senders.resolve("+972501234567") == "ניר"


def test_plus_and_no_plus_canonicalize(tmp_stores):
    memory.remember("המספר 972501234567 הוא של ניר")
    # Whatsapp payloads use no '+', outputs elsewhere may include one.
    assert senders.resolve("972501234567") == "ניר"
    assert senders.resolve("+972501234567") == "ניר"


def test_english_the_number(tmp_stores):
    memory.remember("The number +972501234567 is Nir")
    assert senders.resolve("+972501234567") == "Nir"


def test_english_name_first_possessive(tmp_stores):
    memory.remember("Nir's phone is +972501234567")
    assert senders.resolve("+972501234567") == "Nir"


def test_equals_form(tmp_stores):
    memory.remember("+972501234567 = Dana")
    assert senders.resolve("+972501234567") == "Dana"


def test_refresh_after_new_fact(tmp_stores):
    # Initial resolve on empty memory populates the cache with no mapping.
    assert senders.resolve("+972501234567") == "+972501234567"
    # remember() calls senders.refresh() internally, but assert we also
    # pick up the fact after an explicit refresh().
    memory.remember("המספר 972501234567 הוא של ניר")
    senders.refresh()
    assert senders.resolve("+972501234567") == "ניר"


def test_remember_auto_refreshes_cache(tmp_stores):
    assert senders.resolve("+972501234567") == "+972501234567"
    memory.remember("The number +972501234567 is Nir")
    # No explicit refresh — remember() should have invalidated the cache.
    assert senders.resolve("+972501234567") == "Nir"
