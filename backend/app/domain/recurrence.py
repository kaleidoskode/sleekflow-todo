"""Pure recurrence date arithmetic. No database, no HTTP."""

import calendar
from datetime import datetime, timedelta

from app.domain.enums import RecurrenceUnit


def add_interval(anchor: datetime, unit: RecurrenceUnit, steps: int) -> datetime:
    """Advance `anchor` by `steps` units.

    Month arithmetic clamps to the last valid day of the target month, and is always
    computed from the anchor rather than from the previous result, so a series anchored
    on the 31st returns to the 31st in months that have one.
    """
    if unit is RecurrenceUnit.DAY:
        return anchor + timedelta(days=steps)
    if unit is RecurrenceUnit.WEEK:
        return anchor + timedelta(weeks=steps)
    if unit is RecurrenceUnit.MONTH:
        return _add_months(anchor, steps)
    raise ValueError(f"unsupported recurrence unit: {unit}")


def _add_months(anchor: datetime, months: int) -> datetime:
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return anchor.replace(year=year, month=month, day=min(anchor.day, last_day))


def next_occurrence(
    anchor: datetime,
    unit: RecurrenceUnit,
    interval: int,
    current_index: int,
    now: datetime,
) -> tuple[datetime, int]:
    """Return the (due date, occurrence index) of the occurrence following `current_index`.

    Advances past any intervals already in the past, so completing a long-overdue task
    yields a single future occurrence rather than a backlog of missed ones.
    """
    if interval < 1:
        raise ValueError("recurrence interval must be >= 1")

    index = current_index + 1
    due = add_interval(anchor, unit, interval * index)
    while due <= now:
        index += 1
        due = add_interval(anchor, unit, interval * index)
    return due, index
