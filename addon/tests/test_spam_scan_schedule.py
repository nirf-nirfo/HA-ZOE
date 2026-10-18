"""Item 22: SpamScanSchedule store."""
from datetime import datetime, timezone

import pytest

from app import spam_scan_schedule


def test_defaults_on_first_load(tmp_stores):
    row = spam_scan_schedule.get_schedule()
    assert row.start_hour == 8 and row.end_hour == 20
    assert row.interval_hours == 2
    assert row.enabled is True
    assert row.dry_run is True  # safety-on default


def test_set_schedule_overwrites(tmp_stores):
    row = spam_scan_schedule.set_schedule(9, 18, 3)
    assert (row.start_hour, row.end_hour, row.interval_hours) == (9, 18, 3)
    assert spam_scan_schedule.get_schedule().start_hour == 9


def test_set_schedule_rejects_bad_window(tmp_stores):
    with pytest.raises(ValueError):
        spam_scan_schedule.set_schedule(20, 8, 2)  # end before start
    with pytest.raises(ValueError):
        spam_scan_schedule.set_schedule(0, 24, 0)  # interval too small
    with pytest.raises(ValueError):
        spam_scan_schedule.set_schedule(-1, 10, 2)


def test_within_window_inside(tmp_stores):
    spam_scan_schedule.set_schedule(8, 20, 2)
    # 10:00 Jerusalem → inside
    row = spam_scan_schedule.get_schedule()
    noon_il = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc).astimezone()
    # Build a UTC time that lands at 10:00 Asia/Jerusalem (+03 in summer, +02 in winter;
    # October is +03 so UTC should be 07:00 for a 10:00 local).
    utc_mid = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc)
    assert spam_scan_schedule.within_window(utc_mid, row) is True


def test_within_window_outside(tmp_stores):
    row = spam_scan_schedule.set_schedule(8, 20, 2)
    # 03:00 UTC → ~06:00 Jerusalem, outside 8-20
    early = datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)
    assert spam_scan_schedule.within_window(early, row) is False


def test_within_window_respects_disabled(tmp_stores):
    spam_scan_schedule.set_schedule(8, 20, 2)
    spam_scan_schedule.disable()
    row = spam_scan_schedule.get_schedule()
    noon_il = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
    assert spam_scan_schedule.within_window(noon_il, row) is False


def test_live_mode_toggle(tmp_stores):
    assert spam_scan_schedule.get_schedule().dry_run is True
    row = spam_scan_schedule.enable_live_mode()
    assert row.dry_run is False
    assert row.live_mode_at > 0
    spam_scan_schedule.disable_live_mode()
    assert spam_scan_schedule.get_schedule().dry_run is True


def test_mark_scanned_persists(tmp_stores):
    spam_scan_schedule.mark_scanned(1234567890.0)
    assert spam_scan_schedule.get_schedule().last_scan_at == 1234567890.0
