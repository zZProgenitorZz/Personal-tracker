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


def _month(event: TrackPlayed) -> tuple[int, int]:
    moment = _local(event.played_at)
    return moment.year, moment.month


def _ranked(counter: Counter, name) -> list:
    """Meeste plays eerst; bij gelijkspel op naam."""
    return sorted(counter.items(), key=lambda item: (-item[1], name(item[0]).lower()))


class TopArtistsProjection:
    """Plays per artiest per maand. Een nummer met twee artiesten telt voor allebei."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._months: dict[tuple[int, int], Counter] = defaultdict(Counter)

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            self._months[_month(event)].update(set(event.artists))

    def top(self, year: int, month: int, limit: int = 10) -> list[tuple[str, int]]:
        return _ranked(self._months.get((year, month), Counter()), name=str)[:limit]


@dataclass
class TrackCount:
    track_id: str
    track: str
    artists: list[str]
    plays: int = 0
    minutes: float = 0.0


@dataclass
class _Month:
    counts: Counter = field(default_factory=Counter)
    tracks: dict[str, TrackCount] = field(default_factory=dict)


class TopTracksProjection:
    """Plays per nummer (op track_id) per maand."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._months: dict[tuple[int, int], _Month] = defaultdict(_Month)

    def apply(self, event) -> None:
        if isinstance(event, TrackPlayed):
            month = self._months[_month(event)]
            month.counts[event.track_id] += 1
            entry = month.tracks.setdefault(event.track_id, TrackCount(event.track_id, event.track, list(event.artists)))
            entry.plays += 1
            entry.minutes += listened_ms(event) / 60_000

    def top(self, year: int, month: int, limit: int = 10) -> list[TrackCount]:
        data = self._months.get((year, month))
        if data is None:
            return []
        ranked = _ranked(data.counts, name=lambda track_id: data.tracks[track_id].track)
        return [data.tracks[track_id] for track_id, _ in ranked[:limit]]
