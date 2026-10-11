"""When does a plan fall? Shared by the aggregate (rules) and the projections (agenda).
Repeats are worked out here, not stored as separate events."""
import calendar
import datetime as dt

from .events import Frequency, Repeat


def occurs_on(start: dt.date | None, repeat: Repeat | None, day: dt.date) -> bool:
    """Does a plan that starts on `start` (with a repeat, if any) fall on `day`?"""
    if start is None or day < start:
        return False
    if repeat is None:
        return day == start
    if repeat.until is not None and day > repeat.until:
        return False
    if repeat.every is Frequency.DAILY:
        return True
    if repeat.every is Frequency.WEEKLY:
        return day.weekday() in repeat.weekdays
    last = calendar.monthrange(day.year, day.month)[1]
    return day.day == min(start.day, last)  # MONTHLY


LOOK_AHEAD_DAYS = 400  # a little over a year: enough for any repeat (daily, weekly, monthly)


def next_occurrence(plan, from_day: dt.date) -> dt.date | None:
    """The first day from `from_day` on which `plan` falls and that isn't skipped or ticked off.
    `plan` has day, repeat, done and skipped (a PlanEntry or Plan). None: nothing left, or Someday."""
    if plan.day is None:
        return None
    if plan.repeat is None:
        return plan.day if plan.day >= from_day and plan.day not in plan.done else None
    day = max(from_day, plan.day)
    for _ in range(LOOK_AHEAD_DAYS):
        if plan.repeat.until is not None and day > plan.repeat.until:
            return None
        if occurs_on(plan.day, plan.repeat, day) and day not in plan.skipped and day not in plan.done:
            return day
        day += dt.timedelta(days=1)
    return None


def with_weekdays(start: dt.date, repeat: Repeat | None) -> Repeat | None:
    """Weekly without weekdays = on the weekday of the start date."""
    if repeat is not None and repeat.every is Frequency.WEEKLY and not repeat.weekdays:
        return Repeat(repeat.every, (start.weekday(),), repeat.until)
    if repeat is not None and repeat.every is not Frequency.WEEKLY and repeat.weekdays:
        return Repeat(repeat.every, (), repeat.until)
    return repeat
