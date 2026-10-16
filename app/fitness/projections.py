import datetime as dt
from collections import Counter
from dataclasses import dataclass

from .events import GymArrived, GymLeft, StepsRecorded

MIN_VISIT = dt.timedelta(minutes=10)  # shorter is just walking past
MAX_VISIT = dt.timedelta(hours=4)     # longer (or still open) and the departure was most likely missed
REJOIN_GAP = dt.timedelta(minutes=15)  # an arrival this soon after a departure: the location flickered


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


class StepsProjection:
    """Steps per day: the last number recorded for a day counts."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._steps: dict[dt.date, int] = {}

    def apply(self, event) -> None:
        if isinstance(event, StepsRecorded):
            self._steps[event.day] = event.steps

    def per_day(self) -> dict[dt.date, int]:
        return dict(sorted(self._steps.items()))

    def between(self, start: dt.date, end: dt.date) -> dict[dt.date, int]:
        """The days from `start` up to and including `end` that have steps."""
        return {day: steps for day, steps in self.per_day().items() if start <= day <= end}


@dataclass(frozen=True)
class Visit:
    day: dt.date                 # the day of the arrival (start.date())
    start: dt.datetime           # local time
    end: dt.datetime | None      # None: no departure (yet)
    minutes: int | None          # None: unknown, the departure was missed
    in_progress: bool = False    # no departure yet, but not long ago


class GymProjection:
    """The gym visits. Arrivals and departures may have come in late, twice and in any order, so they
    are sorted by time and each arrival is paired with the next departure. A departure without an
    arrival before it is ignored, and so is one more than MAX_VISIT after the open arrival (that visit
    stays open). An arrival within REJOIN_GAP of a departure continues that visit (the location
    flickers inside a building), before short visits are dropped. Short visits don't count, and a visit without a (believable)
    departure counts as a visit of unknown duration. What is "now" decides that, so the visits are
    worked out per question."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._arrivals: list[dt.datetime] = []
        self._departures: list[dt.datetime] = []

    def apply(self, event) -> None:
        if isinstance(event, GymArrived):
            self._arrivals.append(event.arrived_at)
        elif isinstance(event, GymLeft):
            self._departures.append(event.left_at)

    def _pairs(self) -> list[tuple[dt.datetime, dt.datetime | None]]:
        """(arrival, departure or None), oldest first. A visit runs across midnight like any other."""
        moments = sorted([(t, False) for t in self._arrivals] + [(t, True) for t in self._departures])
        pairs: list[tuple[dt.datetime, dt.datetime | None]] = []
        start = None
        for moment, is_departure in moments:
            if is_departure:
                if start is None:
                    continue  # a departure without an arrival before it
                # Too long after the arrival it is no departure of this visit: that one never got closed.
                pairs.append((start, moment if moment - start <= MAX_VISIT else None))
                start = None
            elif start is None:
                if pairs and pairs[-1][1] is not None and moment - pairs[-1][1] <= REJOIN_GAP:
                    start = pairs.pop()[0]  # the location flickered: the same visit goes on
                else:
                    start = moment
            elif moment - start > MAX_VISIT:  # the departure of the earlier visit never came
                pairs.append((start, None))
                start = moment
            # else: the same visit, arrival reported again
        if start is not None:
            pairs.append((start, None))
        return pairs

    def visits(self, now: dt.datetime) -> list[Visit]:
        """The visits that count, oldest first. `now` is local time without a time zone."""
        visits = (_visit(start, end, now) for start, end in self._pairs())
        return [visit for visit in visits if visit is not None]

    def between(self, start: dt.date, end: dt.date, now: dt.datetime) -> list[Visit]:
        """The visits that began from `start` up to and including `end`."""
        return [v for v in self.visits(now) if start <= v.day <= end]

    def per_day(self, now: dt.datetime) -> dict[dt.date, int]:
        """How many visits per day."""
        return dict(Counter(v.day for v in self.visits(now)))

    def streak_weeks(self, today: dt.date, now: dt.datetime) -> int:
        """Weeks in a row (Monday to Sunday) with at least one visit, up to this week. This week
        doesn't break the streak while it has no visit yet: it isn't over."""
        weeks = {monday_of(v.day) for v in self.visits(now)}
        week = monday_of(today)
        if week not in weeks:
            week -= dt.timedelta(weeks=1)
        streak = 0
        while week in weeks:
            streak += 1
            week -= dt.timedelta(weeks=1)
        return streak


def _visit(start: dt.datetime, end: dt.datetime | None, now: dt.datetime) -> Visit | None:
    length = (end or now) - start
    if length < MIN_VISIT:
        return None
    if length > MAX_VISIT:
        return Visit(start.date(), start, end, None)
    return Visit(start.date(), start, end, int(length.total_seconds() // 60), in_progress=end is None)


@dataclass(frozen=True)
class WeekTotals:
    monday: dt.date
    visits: int
    gym_minutes: int         # only visits with a known duration
    steps: int
    days_with_steps: int

    @property
    def gym_hours(self) -> float:
        return round(self.gym_minutes / 60, 1)

    @property
    def average_steps(self) -> int:
        return round(self.steps / self.days_with_steps) if self.days_with_steps else 0


def week_totals(steps: StepsProjection, gym: GymProjection, monday: dt.date, now: dt.datetime) -> WeekTotals:
    sunday = monday + dt.timedelta(days=6)
    visits = gym.between(monday, sunday, now)
    week_steps = steps.between(monday, sunday)
    return WeekTotals(monday, len(visits), sum(v.minutes or 0 for v in visits), sum(week_steps.values()),
                      len(week_steps))
