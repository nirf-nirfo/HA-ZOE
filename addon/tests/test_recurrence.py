"""Recurrence math: reminders._next_occurrence, reminders.normalize_recurring,
check_ins._next_from_interval, recurring_expenses._due_today.

These are the scheduling primitives that decide when a recurring artifact fires
again. Historically brittle around DST, short months, and Item 01's infinite-loop
guard, so covered here at the function level instead of via the store surface.
"""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import check_ins, recurring_expenses, reminders
from app.recurring_expenses import RecurringExpense

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _il_ts(y, m, d, h=12, mi=0) -> float:
    return datetime(y, m, d, h, mi, tzinfo=_IL_TZ).timestamp()


# ---------- reminders._next_occurrence ---------------------------------------


def test_next_occurrence_daily_advances_one_day():
    base = _il_ts(2026, 3, 1, 9, 0)
    now = base - 60  # just before the reminder is due
    nxt = reminders._next_occurrence(base, "daily", now)
    dt = datetime.fromtimestamp(nxt, _IL_TZ)
    assert (dt.month, dt.day, dt.hour, dt.minute) == (3, 2, 9, 0)


def test_next_occurrence_weekly_advances_one_week():
    base = _il_ts(2026, 3, 1, 9, 0)
    nxt = reminders._next_occurrence(base, "weekly", base - 60)
    dt = datetime.fromtimestamp(nxt, _IL_TZ)
    assert (dt.month, dt.day) == (3, 8)


def test_next_occurrence_monthly_advances_one_month():
    base = _il_ts(2026, 3, 15, 9, 0)
    nxt = reminders._next_occurrence(base, "monthly", base - 60)
    dt = datetime.fromtimestamp(nxt, _IL_TZ)
    assert (dt.month, dt.day) == (4, 15)


def test_next_occurrence_monthly_short_month_clamps():
    """Jan 31 → Feb 28 (or 29) — never rolls over into March."""
    base = _il_ts(2026, 1, 31, 9, 0)
    nxt = reminders._next_occurrence(base, "monthly", base - 60)
    dt = datetime.fromtimestamp(nxt, _IL_TZ)
    assert dt.month == 2
    assert dt.day in (28, 29)


def test_next_occurrence_yearly_advances_one_year():
    base = _il_ts(2026, 5, 10, 9, 0)
    nxt = reminders._next_occurrence(base, "yearly", base - 60)
    dt = datetime.fromtimestamp(nxt, _IL_TZ)
    assert (dt.year, dt.month, dt.day) == (2027, 5, 10)


def test_next_occurrence_yearly_feb29_clamps_in_non_leap_year():
    """Feb 29 2024 → Feb 28 2025 (non-leap)."""
    base = _il_ts(2024, 2, 29, 9, 0)
    nxt = reminders._next_occurrence(base, "yearly", base - 60)
    dt = datetime.fromtimestamp(nxt, _IL_TZ)
    assert (dt.year, dt.month, dt.day) == (2025, 2, 28)


def test_next_occurrence_invalid_recurrence_raises():
    """Item 01 guard: invalid recurrence must raise instead of infinite-looping
    (`_add_period` would return dt unchanged and the while loop would spin)."""
    base = _il_ts(2026, 3, 1, 9, 0)
    with pytest.raises(ValueError):
        reminders._next_occurrence(base, "hourly", base + 60)


def test_next_occurrence_catches_up_past_missed_ticks():
    """A daily reminder whose stored send_at is a week ago should advance to a
    future date, not stop at the first past occurrence."""
    week_ago = _il_ts(2026, 1, 1, 9, 0)
    now = _il_ts(2026, 1, 8, 12, 0)
    nxt = reminders._next_occurrence(week_ago, "daily", now)
    assert nxt > now


# ---------- reminders.normalize_recurring -----------------------------------


def test_normalize_recurring_pulls_yearly_back_when_year_was_wrong(tmp_stores):
    """A yearly reminder created with next occurrence "next year" when this year
    is still valid must be pulled to this year."""
    now = time.time()
    # Same month/day next year vs this year
    next_year_ts = datetime.fromtimestamp(now, _IL_TZ).replace(
        year=datetime.fromtimestamp(now, _IL_TZ).year + 1
    ).timestamp() + 3600  # 1h after "now next year"
    r = reminders.add_reminder("s1", "birthday", send_at=next_year_ts, recurrence="yearly")
    changed = reminders.normalize_recurring()
    assert changed == 1
    # The stored reminder now fires within the next year, not next-next-year.
    updated = [x for x in reminders._load() if x.id == r.id][0]
    assert updated.send_at < next_year_ts


def test_normalize_recurring_leaves_correct_yearly_alone(tmp_stores):
    """If the yearly is already at its next natural occurrence, don't move it."""
    now = time.time()
    future = now + 30 * 86400  # 30 days out
    reminders.add_reminder("s1", "later", send_at=future, recurrence="yearly")
    changed = reminders.normalize_recurring()
    assert changed == 0


# ---------- check_ins._next_from_interval -----------------------------------


def test_next_from_interval_advances_by_interval():
    nxt = check_ins._next_from_interval(1000.0, 15, now=1001.0)
    # 1000 + 15*60 = 1900; 1900 > 1001, so first step wins.
    assert nxt == 1900.0


def test_next_from_interval_catches_up_across_missed_ticks():
    """Missed a couple of ticks (add-on down). Should walk forward past 'now'
    in interval steps, not jump to now + interval or return the past."""
    next_at = 1000.0
    interval = 10  # minutes
    now = 1000.0 + 3 * 600 + 30  # 3.05 intervals in the future
    nxt = check_ins._next_from_interval(next_at, interval, now=now)
    assert nxt > now
    # And the gap between now and nxt is at most one interval.
    assert nxt - now <= interval * 60


# ---------- recurring_expenses._due_today -----------------------------------


def _rx(day: int, pattern: str = "monthly") -> RecurringExpense:
    return RecurringExpense(
        id="x",
        name="rent",
        amount=100.0,
        day_of_month=day,
        month_pattern=pattern,
        category="חשבונות",
        payment_method="לא צוין",
        last_inserted_date=None,
        created_at=0.0,
    )


def test_due_today_monthly_matches_any_month():
    r = _rx(15, "monthly")
    assert recurring_expenses._due_today(r, datetime(2026, 3, 15)) is True
    assert recurring_expenses._due_today(r, datetime(2026, 7, 15)) is True


def test_due_today_monthly_wrong_day():
    r = _rx(15, "monthly")
    assert recurring_expenses._due_today(r, datetime(2026, 3, 16)) is False


def test_due_today_month_pattern_jan_jul_matches_only_those():
    r = _rx(15, "jan,jul")
    assert recurring_expenses._due_today(r, datetime(2026, 1, 15)) is True
    assert recurring_expenses._due_today(r, datetime(2026, 7, 15)) is True
    assert recurring_expenses._due_today(r, datetime(2026, 3, 15)) is False


def test_due_today_short_month_clamp_feb_non_leap():
    """day_of_month=31 must fire on Feb 28 in a non-leap year."""
    r = _rx(31, "monthly")
    assert recurring_expenses._due_today(r, datetime(2026, 2, 28)) is True
    # But not on Feb 27
    assert recurring_expenses._due_today(r, datetime(2026, 2, 27)) is False


def test_due_today_short_month_clamp_feb_leap():
    """day_of_month=31 must fire on Feb 29 in a leap year, not Feb 28."""
    r = _rx(31, "monthly")
    assert recurring_expenses._due_today(r, datetime(2024, 2, 29)) is True
    assert recurring_expenses._due_today(r, datetime(2024, 2, 28)) is False
