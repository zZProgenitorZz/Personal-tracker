import calendar
from bisect import insort
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from .events import TrackPlayed

RECENT_LIMIT = 50


def listened_ms(event: TrackPlayed) -> int:
    """Really listened if that is known (export), otherwise the length of the track (API)."""
    return event.ms_played if event.ms_played is not None else event.duration_ms


def _local(moment: datetime) -> datetime:
    return moment.astimezone()  # days and months follow your time zone, not UTC


@dataclass(frozen=True)
class RecentPlay:
    played_at: datetime
    track: str
    artists: list[str]
    album: str
    track_id: str
    minutes: float


class RecentlyPlayedProjection:
    """The last RECENT_LIMIT plays, newest first."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._plays: list[tuple[datetime, RecentPlay]] = []  # ascending by time

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            play = RecentPlay(event.played_at, event.track, list(event.artists), event.album, event.track_id,
                              listened_ms(event) / 60_000)
            insort(self._plays, (event.played_at, play), key=lambda p: p[0])
            del self._plays[:-RECENT_LIMIT]

    def recent(self) -> list[RecentPlay]:
        return [play for _, play in reversed(self._plays)]

    def latest_played_at(self) -> datetime | None:
        """The newest moment; the sync only asks Spotify for plays after it."""
        return self._plays[-1][0] if self._plays else None


class ListeningActivityProjection:
    """Minutes per day and per week (ISO week)."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._ms_per_day: dict[date, int] = defaultdict(int)
        self._plays_per_day: dict[date, int] = defaultdict(int)

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            day = _local(event.played_at).date()
            self._ms_per_day[day] += listened_ms(event)
            self._plays_per_day[day] += 1

    def minutes_per_day(self) -> dict[date, float]:
        return {day: ms / 60_000 for day, ms in sorted(self._ms_per_day.items())}

    def plays_per_day(self) -> dict[date, int]:
        return dict(sorted(self._plays_per_day.items()))

    def minutes_per_week(self) -> dict[str, float]:
        weeks: dict[str, int] = defaultdict(int)
        for day, ms in self._ms_per_day.items():
            year, week, _ = day.isocalendar()
            weeks[f"{year}-W{week:02d}"] += ms
        return {week: ms / 60_000 for week, ms in sorted(weeks.items())}


def _ranked(counter: Counter, name) -> list:
    """Most plays first; on a tie by name."""
    return sorted(counter.items(), key=lambda item: (-item[1], name(item[0]).lower()))


def _tied_first(counter: Counter) -> int:
    """How many share first place (most plays); 0 when nothing was played."""
    top = max(counter.values(), default=0)
    return sum(n == top for n in counter.values()) if top else 0


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


class TopArtistsProjection:
    """Plays per artist, kept per day: that way any period works (a month, or 1 Sep - 15 Oct).
    A track with two artists counts for both."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._days: dict[date, Counter] = defaultdict(Counter)

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            self._days[_local(event.played_at).date()].update(set(event.artists))

    def between(self, start: date, end: date, limit: int = 10) -> list[tuple[str, int]]:
        """From `start` up to and including `end` (local days)."""
        return _ranked(self._total(start, end), name=str)[:limit]

    def top(self, year: int, month: int, limit: int = 10) -> list[tuple[str, int]]:
        return self.between(*_month_range(year, month), limit=limit)

    def count(self, year: int, month: int) -> int:
        """How many different artists you listened to that month."""
        return len(self._total(*_month_range(year, month)))

    def tied_first(self, start: date, end: date) -> int:
        """How many artists share first place from `start` up to and including `end`."""
        return _tied_first(self._total(start, end))

    def _total(self, start: date, end: date) -> Counter:
        total: Counter = Counter()
        for day, counts in self._days.items():
            if start <= day <= end:
                total.update(counts)
        return total


@dataclass
class TrackCount:
    track_id: str
    track: str
    artists: list[str]
    plays: int = 0
    minutes: float = 0.0


class TopTracksProjection:
    """Plays per track (by track_id), kept per day: that way any period works."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._days: dict[date, dict[str, list]] = defaultdict(dict)  # day -> track_id -> [plays, minutes]
        self._names: dict[str, tuple[str, list[str]]] = {}

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            entry = self._days[_local(event.played_at).date()].setdefault(event.track_id, [0, 0.0])
            entry[0] += 1
            entry[1] += listened_ms(event) / 60_000
            self._names.setdefault(event.track_id, (event.track, list(event.artists)))

    def between(self, start: date, end: date, limit: int = 10) -> list[TrackCount]:
        """From `start` up to and including `end` (local days); most plays first."""
        totals = self._totals(start, end)
        ranked = _ranked(Counter({t: e.plays for t, e in totals.items()}), name=lambda t: totals[t].track)
        return [totals[track_id] for track_id, _ in ranked[:limit]]

    def tied_first(self, start: date, end: date) -> int:
        """How many tracks share first place from `start` up to and including `end`."""
        return _tied_first(Counter({t: e.plays for t, e in self._totals(start, end).items()}))

    def _totals(self, start: date, end: date) -> dict[str, TrackCount]:
        totals: dict[str, TrackCount] = {}
        for day in sorted(d for d in self._days if start <= d <= end):
            for track_id, (plays, minutes) in self._days[day].items():
                track, artists = self._names[track_id]
                entry = totals.setdefault(track_id, TrackCount(track_id, track, list(artists)))
                entry.plays += plays
                entry.minutes += minutes
        return totals

    def top(self, year: int, month: int, limit: int = 10) -> list[TrackCount]:
        return self.between(*_month_range(year, month), limit=limit)
