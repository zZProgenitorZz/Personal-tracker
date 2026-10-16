"""Events of Fitness: steps per day and visits to the gym. Class names are unique across all
domains, because the event store only stores the class name.

Nothing is logged by hand: the iPhone sends it (Apple Shortcuts). `day`, `arrived_at` and
`left_at` are local time (this PC's clock, no time zone), like Planner; `at` (when Progen
recorded it) is UTC.
"""
import datetime as dt
from dataclasses import dataclass, field


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class StepsRecorded:
    """The steps of one day; the newest event for a day is the one that counts."""
    day: dt.date
    steps: int
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class GymArrived:
    day: dt.date  # the day of arrived_at
    arrived_at: dt.datetime
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class GymLeft:
    day: dt.date  # the day of left_at
    left_at: dt.datetime
    at: dt.datetime = field(default_factory=now)


EVENT_TYPES = [StepsRecorded, GymArrived, GymLeft]
