"""Item 10: memory-aware briefing composition — fallback-path tests.

Covers only the deterministic side of the split:
- with `settings.briefing_model_compose = False` (the shipped default),
  `_compile_morning_briefing` / `_compile_evening_briefing` produce the
  same text they did before Item 10;
- data gatherers are pure — same inputs give the same dict;
- an exception from run_briefing_model with the flag on falls back to
  the deterministic renderer instead of propagating.

The model call itself lives in the behavior harness (Item 17); this
file is intentionally offline.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import main as app_main
from app.settings import settings


_IL_TZ = ZoneInfo("Asia/Jerusalem")


@pytest.fixture(autouse=True)
def _stub_holidays(monkeypatch):
    """Holidays fetch normally hits the Hebcal cache on disk — stub it so the
    tests are hermetic and don't need a fixture holiday file."""
    async def _no_holidays(date):
        return []
    monkeypatch.setattr("app.main.holidays.holidays_for_date", _no_holidays)


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(settings, "briefing_model_compose", False)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "briefing_model_compose", True)


def _dt(y, m, d, hh=8, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=_IL_TZ)


@pytest.mark.asyncio
async def test_morning_flag_off_matches_deterministic_renderer(tmp_stores, flag_off):
    """Flag off: the compile function returns exactly the deterministic
    renderer's output for the same gathered data."""
    dt = _dt(2026, 9, 30)
    text = await app_main._compile_morning_briefing("s1", dt)
    data = await app_main._gather_morning_data("s1", dt)
    assert text == app_main._render_morning_deterministic(data)


@pytest.mark.asyncio
async def test_evening_flag_off_matches_deterministic_renderer(tmp_stores, flag_off):
    today = _dt(2026, 9, 30)
    tomorrow = _dt(2026, 10, 1)
    text = await app_main._compile_evening_briefing("s1", today, tomorrow)
    data = await app_main._gather_evening_data("s1", today, tomorrow)
    assert text == app_main._render_evening_deterministic(data)


@pytest.mark.asyncio
async def test_gatherers_are_pure_same_input_same_output(tmp_stores):
    """Same inputs -> same dict. Also documents the shape callers depend on."""
    dt = _dt(2026, 9, 30)
    a = await app_main._gather_morning_data("s1", dt)
    b = await app_main._gather_morning_data("s1", dt)
    assert a == b

    today = _dt(2026, 9, 30)
    tomorrow = _dt(2026, 10, 1)
    c = await app_main._gather_evening_data("s1", today, tomorrow)
    d = await app_main._gather_evening_data("s1", today, tomorrow)
    assert c == d
    assert "tomorrow" in c and c["tomorrow"]["date"] == "2026-10-01"


@pytest.mark.asyncio
async def test_morning_flag_on_falls_back_when_model_raises(tmp_stores, flag_on, monkeypatch):
    """Any exception from run_briefing_model -> deterministic output. A slow
    or failing Anthropic call must never hold up the daily brief loop."""
    def _boom(kind, data):
        raise RuntimeError("simulated Anthropic failure")

    monkeypatch.setattr("app.main.run_briefing_model", _boom)
    dt = _dt(2026, 9, 30)
    text = await app_main._compile_morning_briefing("s1", dt)
    data = await app_main._gather_morning_data("s1", dt)
    assert text == app_main._render_morning_deterministic(data)


@pytest.mark.asyncio
async def test_evening_flag_on_falls_back_when_model_raises(tmp_stores, flag_on, monkeypatch):
    def _boom(kind, data):
        raise RuntimeError("simulated Anthropic failure")

    monkeypatch.setattr("app.main.run_briefing_model", _boom)
    today = _dt(2026, 9, 30)
    tomorrow = _dt(2026, 10, 1)
    text = await app_main._compile_evening_briefing("s1", today, tomorrow)
    data = await app_main._gather_evening_data("s1", today, tomorrow)
    assert text == app_main._render_evening_deterministic(data)


@pytest.mark.asyncio
async def test_morning_flag_on_uses_model_text_when_it_returns(tmp_stores, flag_on, monkeypatch):
    """When the compose call succeeds, its text is returned verbatim."""
    def _fake(kind, data):
        assert kind == "morning"
        assert isinstance(data, dict) and "hebrew_day" in data
        return "מודל אמר: בוקר טוב"

    monkeypatch.setattr("app.main.run_briefing_model", _fake)
    text = await app_main._compile_morning_briefing("s1", _dt(2026, 9, 30))
    assert text == "מודל אמר: בוקר טוב"


def test_resolve_sender_name_reads_memory_fact(tmp_stores):
    """Best-effort until Item 13 lands: pulls the name from a memory fact
    matching the "המספר X הוא של Y" phrasing."""
    from app import memory

    memory.remember("המספר 972501234567 הוא של ניר")
    assert app_main._resolve_sender_name("972501234567") == "ניר"


def test_resolve_sender_name_missing_returns_none(tmp_stores):
    assert app_main._resolve_sender_name("972509999999") is None
