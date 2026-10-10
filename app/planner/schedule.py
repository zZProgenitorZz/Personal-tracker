"""Wanneer valt een plan? Gedeeld door de aggregate (regels) en de projecties (agenda).
Herhalingen worden hier uitgerekend, niet als losse events opgeslagen."""
import calendar
import datetime as dt

from .events import Frequency, Repeat


def local_now() -> dt.datetime:
    """De klok van deze pc, zonder tijdzone: plannen staan in lokale tijd. Tests zetten hem vast."""
    return dt.datetime.now()


def occurs_on(start: dt.date | None, repeat: Repeat | None, day: dt.date) -> bool:
    """Valt een plan dat begint op `start` (met eventueel een herhaling) op `day`?"""
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


LOOK_AHEAD_DAYS = 400  # ruim een jaar: genoeg voor elke herhaling (dagelijks, wekelijks, maandelijks)


def next_occurrence(plan, from_day: dt.date) -> dt.date | None:
    """De eerste dag vanaf `from_day` waarop `plan` valt en die niet is overgeslagen of afgevinkt.
    `plan` heeft day, repeat, done en skipped (een PlanEntry of Plan). None: niets meer, of Someday."""
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
    """Wekelijks zonder weekdagen = op de weekdag van de begindatum."""
    if repeat is not None and repeat.every is Frequency.WEEKLY and not repeat.weekdays:
        return Repeat(repeat.every, (start.weekday(),), repeat.until)
    if repeat is not None and repeat.every is not Frequency.WEEKLY and repeat.weekdays:
        return Repeat(repeat.every, (), repeat.until)
    return repeat
