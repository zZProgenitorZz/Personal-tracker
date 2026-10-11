import calendar
from bisect import insort
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from .events import TrackPlayed

RECENT_LIMIT = 50


def listened_ms(event: TrackPlayed) -> int:
    """Echt geluisterd als dat bekend is (export), anders de lengte van het nummer (API)."""
    return event.ms_played if event.ms_played is not None else event.duration_ms


def _local(moment: datetime) -> datetime:
    return moment.astimezone()  # dagen en maanden volgen jouw tijdzone, niet UTC


@dataclass(frozen=True)
class RecentPlay:
    played_at: datetime
    track: str
    artists: list[str]
    album: str
    track_id: str
    minutes: float


class RecentlyPlayedProjection:
    """De laatste RECENT_LIMIT plays, nieuwste eerst."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._plays: list[tuple[datetime, RecentPlay]] = []  # oplopend op tijd

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            play = RecentPlay(event.played_at, event.track, list(event.artists), event.album, event.track_id,
                              listened_ms(event) / 60_000)
            insort(self._plays, (event.played_at, play), key=lambda p: p[0])
            del self._plays[:-RECENT_LIMIT]

    def recent(self) -> list[RecentPlay]:
        return [play for _, play in reversed(self._plays)]

    def latest_played_at(self) -> datetime | None:
        """Het nieuwste moment; de sync vraagt Spotify alleen om plays daarna."""
        return self._plays[-1][0] if self._plays else None


class ListeningActivityProjection:
    """Minuten per dag en per week (ISO-week)."""

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
    """Meeste plays eerst; bij gelijkspel op naam."""
    return sorted(counter.items(), key=lambda item: (-item[1], name(item[0]).lower()))


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


class TopArtistsProjection:
    """Plays per artiest, per dag bijgehouden: zo kan elke periode (een maand, of 1 sep - 15 okt).
    Een nummer met twee artiesten telt voor allebei."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._days: dict[date, Counter] = defaultdict(Counter)

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            self._days[_local(event.played_at).date()].update(set(event.artists))

    def between(self, start: date, end: date, limit: int = 10) -> list[tuple[str, int]]:
        """Van `start` tot en met `end` (lokale dagen)."""
        total: Counter = Counter()
        for day, counts in self._days.items():
            if start <= day <= end:
                total.update(counts)
        return _ranked(total, name=str)[:limit]

    def top(self, year: int, month: int, limit: int = 10) -> list[tuple[str, int]]:
        return self.between(*_month_range(year, month), limit=limit)


@dataclass
class TrackCount:
    track_id: str
    track: str
    artists: list[str]
    plays: int = 0
    minutes: float = 0.0


class TopTracksProjection:
    """Plays per nummer (op track_id), per dag bijgehouden: zo kan elke periode."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._days: dict[date, dict[str, list]] = defaultdict(dict)  # dag -> track_id -> [plays, minuten]
        self._names: dict[str, tuple[str, list[str]]] = {}

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            entry = self._days[_local(event.played_at).date()].setdefault(event.track_id, [0, 0.0])
            entry[0] += 1
            entry[1] += listened_ms(event) / 60_000
            self._names.setdefault(event.track_id, (event.track, list(event.artists)))

    def between(self, start: date, end: date, limit: int = 10) -> list[TrackCount]:
        """Van `start` tot en met `end` (lokale dagen); meeste plays eerst."""
        totals: dict[str, TrackCount] = {}
        for day in sorted(d for d in self._days if start <= d <= end):
            for track_id, (plays, minutes) in self._days[day].items():
                track, artists = self._names[track_id]
                entry = totals.setdefault(track_id, TrackCount(track_id, track, list(artists)))
                entry.plays += plays
                entry.minutes += minutes
        ranked = _ranked(Counter({t: e.plays for t, e in totals.items()}), name=lambda t: totals[t].track)
        return [totals[track_id] for track_id, _ in ranked[:limit]]

    def top(self, year: int, month: int, limit: int = 10) -> list[TrackCount]:
        return self.between(*_month_range(year, month), limit=limit)
