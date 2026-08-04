from datetime import UTC, datetime

import pytest

from app.domain.enums import RecurrenceUnit
from app.domain.recurrence import add_interval, next_occurrence


def dt(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=UTC)


def test_add_days():
    assert add_interval(dt(2026, 1, 1), RecurrenceUnit.DAY, 3) == dt(2026, 1, 4)


def test_add_weeks():
    assert add_interval(dt(2026, 1, 1), RecurrenceUnit.WEEK, 2) == dt(2026, 1, 15)


def test_add_months_simple():
    assert add_interval(dt(2026, 1, 15), RecurrenceUnit.MONTH, 1) == dt(2026, 2, 15)


def test_add_months_clamps_to_shorter_month():
    assert add_interval(dt(2026, 1, 31), RecurrenceUnit.MONTH, 1) == dt(2026, 2, 28)


def test_add_months_clamps_to_leap_february():
    assert add_interval(dt(2028, 1, 31), RecurrenceUnit.MONTH, 1) == dt(2028, 2, 29)


def test_add_months_crosses_year_boundary():
    assert add_interval(dt(2026, 11, 30), RecurrenceUnit.MONTH, 3) == dt(2027, 2, 28)


def test_monthly_series_does_not_drift_off_month_end():
    """The whole reason for anchor+index: 31 Jan must return to 31 Mar, not 28 Mar."""
    anchor = dt(2026, 1, 31)
    assert add_interval(anchor, RecurrenceUnit.MONTH, 1) == dt(2026, 2, 28)
    assert add_interval(anchor, RecurrenceUnit.MONTH, 2) == dt(2026, 3, 31)


def test_next_occurrence_advances_one_interval_when_future():
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 1, 10)
    due, index = next_occurrence(anchor, RecurrenceUnit.WEEK, 1, 0, now)
    assert due == dt(2026, 6, 8)
    assert index == 1


def test_next_occurrence_rolls_forward_past_missed_intervals():
    """Completing a weekly task 3 weeks late must not spawn a backdated occurrence."""
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 25, 12)
    due, index = next_occurrence(anchor, RecurrenceUnit.WEEK, 1, 0, now)
    assert due == dt(2026, 6, 29)
    assert index == 4


def test_next_occurrence_respects_custom_interval():
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 1, 10)
    due, index = next_occurrence(anchor, RecurrenceUnit.DAY, 3, 0, now)
    assert due == dt(2026, 6, 4)
    assert index == 1


def test_next_occurrence_continues_from_current_index():
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 8, 10)
    due, index = next_occurrence(anchor, RecurrenceUnit.WEEK, 1, 1, now)
    assert due == dt(2026, 6, 15)
    assert index == 2


def test_next_occurrence_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        next_occurrence(dt(2026, 6, 1), RecurrenceUnit.DAY, 0, 0, dt(2026, 6, 1))
