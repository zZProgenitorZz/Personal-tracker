import datetime as dt

from ..domain import DomainError
from .events import GymArrived, GymLeft, StepsRecorded

MAX_STEPS = 200_000
SAME_MOMENT = dt.timedelta(minutes=1)  # a message resent with a slightly different time is the same one
CLOCK_SLACK = dt.timedelta(minutes=5)  # a phone clock may run a little ahead of this PC


def steps_stream(day: dt.date) -> str:
    return f"steps-{day.isoformat()}"


def gym_stream(day: dt.date) -> str:
    return f"gym-{day.isoformat()}"


class StepsDay:
    """The steps of one day, built from its events (stream "steps-<day>")."""

    def __init__(self, day: dt.date, events: list):
        self.day = day
        self.steps: int | None = None
        for event in events:
            self._apply(event)

    @property
    def stream_id(self) -> str:
        return steps_stream(self.day)

    def _apply(self, event) -> None:
        if isinstance(event, StepsRecorded):
            self.steps = event.steps

    def record(self, steps: int, today: dt.date) -> list:
        if self.day > today:
            raise DomainError(f"{self.day:%d-%m-%Y} lies in the future; steps can't be recorded yet")
        if not 0 <= steps <= MAX_STEPS:
            raise DomainError(f"Steps on {self.day:%d-%m-%Y} must be between 0 and {MAX_STEPS:,}, not {steps:,}")
        if steps == self.steps:
            return []  # the same number again: no event
        event = StepsRecorded(self.day, steps)
        self._apply(event)
        return [event]


class GymDay:
    """The arrivals and departures with a time on one day (stream "gym-<day>"). They may come in late,
    twice and in any order (the iPhone resends its notes), so there is no pairing and no order here:
    the read model pairs them up. A time within a minute of a known one is the same message again."""

    def __init__(self, day: dt.date, events: list):
        self.day = day
        self.arrivals: list[dt.datetime] = []
        self.departures: list[dt.datetime] = []
        for event in events:
            self._apply(event)

    @property
    def stream_id(self) -> str:
        return gym_stream(self.day)

    def _apply(self, event) -> None:
        if isinstance(event, GymArrived):
            self.arrivals.append(event.arrived_at)
        elif isinstance(event, GymLeft):
            self.departures.append(event.left_at)

    def arrive(self, arrived_at: dt.datetime, now: dt.datetime) -> list:
        _check_not_future(arrived_at, now)
        if _known(self.arrivals, arrived_at):
            return []
        return self._record(GymArrived(self.day, arrived_at))

    def leave(self, left_at: dt.datetime, now: dt.datetime) -> list:
        _check_not_future(left_at, now)
        if _known(self.departures, left_at):
            return []
        return self._record(GymLeft(self.day, left_at))

    def _record(self, event) -> list:
        self._apply(event)
        return [event]


def _known(times: list[dt.datetime], moment: dt.datetime) -> bool:
    return any(abs(moment - known) <= SAME_MOMENT for known in times)


def _check_not_future(moment: dt.datetime, now: dt.datetime) -> None:
    if moment > now + CLOCK_SLACK:
        raise DomainError(f"{moment:%d-%m-%Y %H:%M} lies in the future")
