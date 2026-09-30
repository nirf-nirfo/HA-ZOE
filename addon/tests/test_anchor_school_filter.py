"""Item 11: anchor tagging + school-off suppression.

Covers the four pieces of the item that can be tested offline:
  * default tags on new anchors and persistence of an explicit tag list;
  * the idempotent `auto_tag_school` migration;
  * briefing gather filtering school anchors and emitting a note on a
    school-off holiday day;
  * gather leaves everything alone on a normal day.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import anchors
from app import main as app_main


_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _dt(y, m, d):
    return datetime(y, m, d, 8, 0, tzinfo=_IL_TZ)


# --- anchor field ---------------------------------------------------------

def test_new_anchor_has_empty_tags_by_default(tmp_stores):
    a = anchors.add_anchor("sunday", "school ends", "13:00")
    assert a.tags == []
    assert anchors.list_all()[0].tags == []


def test_add_anchor_persists_school_tag(tmp_stores):
    anchors.add_anchor("sunday", "school ends", "13:00", tags=["school"])
    got = anchors.list_all()
    assert got[0].tags == ["school"]


def test_add_anchor_ignores_empty_tag_list(tmp_stores):
    # tags=None and tags=[] both mean "no tags" — the default_factory list
    # is not shared across rows.
    a = anchors.add_anchor("monday", "gym", None, tags=None)
    b = anchors.add_anchor("tuesday", "yoga", None, tags=[])
    assert a.tags == [] and b.tags == []
    a.tags.append("mine")
    assert b.tags == []


# --- auto_tag migration ---------------------------------------------------

def test_auto_tag_school_marks_school_related_texts(tmp_stores):
    anchors.add_anchor("sunday", "מילי מסיימת בית ספר", "13:00")
    anchors.add_anchor("monday", "school pickup", "13:00")
    anchors.add_anchor("tuesday", "כיתה ב מסיימת", "13:00")
    anchors.add_anchor("wednesday", "soccer", "17:00")  # unrelated

    tagged = anchors.auto_tag_school()
    assert tagged == 3
    by_day = {a.day: a for a in anchors.list_all()}
    assert "school" in by_day["sunday"].tags
    assert "school" in by_day["monday"].tags
    assert "school" in by_day["tuesday"].tags
    assert "school" not in by_day["wednesday"].tags


def test_auto_tag_school_is_idempotent(tmp_stores):
    anchors.add_anchor("sunday", "מילי מסיימת בית ספר", "13:00")
    anchors.auto_tag_school()
    # Second pass touches nothing and does not duplicate the tag.
    assert anchors.auto_tag_school() == 0
    assert anchors.list_all()[0].tags == ["school"]


# --- briefing gather filter ----------------------------------------------

def _stub_holidays(monkeypatch, hols):
    async def _fetch(_date):
        return hols
    monkeypatch.setattr("app.main.holidays.holidays_for_date", _fetch)


@pytest.mark.asyncio
async def test_gather_filters_school_anchors_on_school_off_day(tmp_stores, monkeypatch):
    anchors.add_anchor("sunday", "school ends", "13:00", tags=["school"])
    anchors.add_anchor("sunday", "soccer", "17:00")
    _stub_holidays(monkeypatch, [
        {"title": "חול המועד סוכות", "school_status": "off"},
    ])
    data = await app_main._gather_morning_data("s1", _dt(2026, 10, 4))
    texts = [a["text"] for a in data["anchors"]]
    assert texts == ["soccer"]
    assert "חול המועד" in data["school_off_note"]
    assert "אין בית ספר" in data["school_off_note"]


@pytest.mark.asyncio
async def test_gather_normal_day_keeps_everything(tmp_stores, monkeypatch):
    anchors.add_anchor("sunday", "school ends", "13:00", tags=["school"])
    anchors.add_anchor("sunday", "soccer", "17:00")
    _stub_holidays(monkeypatch, [])
    data = await app_main._gather_morning_data("s1", _dt(2026, 10, 4))
    texts = sorted(a["text"] for a in data["anchors"])
    assert texts == ["school ends", "soccer"]
    assert "school_off_note" not in data


@pytest.mark.asyncio
async def test_render_prepends_school_off_note(tmp_stores, monkeypatch):
    anchors.add_anchor("sunday", "school ends", "13:00", tags=["school"])
    _stub_holidays(monkeypatch, [
        {"title": "חול המועד סוכות", "school_status": "off"},
    ])
    data = await app_main._gather_morning_data("s1", _dt(2026, 10, 4))
    text = app_main._render_morning_deterministic(data)
    assert "🎒" in text
    assert "אין בית ספר" in text
    # school anchor got filtered out of the brief entirely
    assert "school ends" not in text


@pytest.mark.asyncio
async def test_evening_tomorrow_uses_tomorrows_holidays(tmp_stores, monkeypatch):
    """Tomorrow's school anchors are filtered against tomorrow's holidays,
    not today's — _gather_day_data is called separately for each date."""
    anchors.add_anchor("sunday", "today anchor", "10:00")
    anchors.add_anchor("monday", "school ends", "13:00", tags=["school"])

    async def _fetch(date):
        # Sunday 2026-10-04 is a normal day; Monday 2026-10-05 is school-off.
        if date == "2026-10-05":
            return [{"title": "חול המועד סוכות", "school_status": "off"}]
        return []
    monkeypatch.setattr("app.main.holidays.holidays_for_date", _fetch)

    data = await app_main._gather_evening_data("s1", _dt(2026, 10, 4), _dt(2026, 10, 5))
    assert [a["text"] for a in data["anchors"]] == ["today anchor"]
    tomorrow = data["tomorrow"]
    assert [a["text"] for a in tomorrow["anchors"]] == []
    assert "אין בית ספר" in tomorrow["school_off_note"]
