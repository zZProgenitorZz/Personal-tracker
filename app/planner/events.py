"""Events of Planner: appointments and plans. Class names are unique across all domains,
because the event store only stores the class name.

A plan's date and time are local time (this PC's clock), not UTC like `at`
(when Progen recorded it). No time = all day; no date = "Someday".
"""
import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Frequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"  # on the same day of the month; for the 31st in a short month the last day


@dataclass(frozen=True)
class Repeat:
    every: Frequency
    weekdays: tuple[int, ...] = ()  # only with WEEKLY: 0 = Monday ... 6 = Sunday
    until: dt.date | None = None    # the last day it can still fall on


@dataclass(frozen=True)
class PlanAdded:
    plan_id: str
    title: str
    day: dt.date | None
    time: dt.time | None
    duration_min: int | None
    note: str
    repeat: Repeat | None
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanRescheduled:
    """Moved, scheduled or back to Someday (day None)."""
    plan_id: str
    day: dt.date | None
    time: dt.time | None
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanEdited:
    """The complete new title, note, duration and repeat."""
    plan_id: str
    title: str
    note: str
    duration_min: int | None
    repeat: Repeat | None
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanDone:
    plan_id: str
    on: dt.date  # with a repeat: which occurrence
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanReopened:
    plan_id: str
    on: dt.date
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanSkipped:
    """Skips one occurrence of a repeat."""
    plan_id: str
    on: dt.date
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanRemoved:
    plan_id: str
    title: str
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class ReminderSettingsChanged:
    """The complete new reminder setting (one for all plans), in its own stream."""
    first_enabled: bool
    first_minutes: int      # how many minutes before
    second_enabled: bool
    second_minutes: int
    all_day_time: dt.time   # when reminders for all-day plans come
    at: dt.datetime = field(default_factory=now)


EVENT_TYPES = [PlanAdded, PlanRescheduled, PlanEdited, PlanDone, PlanReopened, PlanSkipped, PlanRemoved,
               ReminderSettingsChanged]
