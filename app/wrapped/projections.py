"""Wrapped: a year in review across Reading, Watching and Listening.

Read only. This projection builds itself from the events that already exist, so it
works right away with all old data and has no events or commands of its own. Days,
months and years follow the local time zone, like the other projections.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from ..domain import title_key
from ..listening.events import TrackPlayed
from ..listening.projections import listened_ms
from ..reading.events import (
    GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged, is_start_status,
)
from ..reading.projections import chapters_read
from ..watching.events import ShowAdded, ShowGenresChanged, ShowStatusChanged, WatchKind, WatchStatus

TOP = 5
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _day(moment: datetime) -> date:
    return moment.astimezone().date()


@dataclass(frozen=True)
class Title:
    title: str
    kind: Kind | WatchKind
    cover: str | None = None


@dataclass(frozen=True)
class TrackTotal:
    track: str
    artists: tuple[str, ...]
    plays: int


@dataclass(frozen=True)
class MonthRow:
    month: int
    label: str
    chapters: float
    finished: int
    minutes: float
    activity: float  # chapters + finished titles + tracks played


@dataclass(frozen=True)
class YearReview:
    year: int
    # Reading
    chapters: float
    top_series: list[tuple[Title, float]]
    reading_genres: list[tuple[str, float]]
    series_started: int
    series_completed: int
    longest_streak: int
    # Watching
    watched_by_kind: dict[WatchKind, int]
    watch_genres: list[tuple[str, int]]
    watched: list[Title]
    # Listening
    listen_minutes: float
    top_artists: list[tuple[str, int]]
    top_tracks: list[TrackTotal]
    busiest_listening_day: tuple[date, float] | None
    # Everything together
    months: list[MonthRow]
    busiest_month: MonthRow | None
    most_active_day: tuple[date, float] | None

    @property
    def watched_total(self) -> int:
        return len(self.watched)

    @property
    def finished(self) -> int:
        return self.series_completed + self.watched_total

    @property
    def has_data(self) -> bool:
        return bool(self.chapters or self.finished or self.listen_minutes or self.series_started)


@dataclass
class _Year:
    chapters_per_day: dict[date, float] = field(default_factory=lambda: defaultdict(float))
    chapters_per_series: Counter = field(default_factory=Counter)
    # Started and finished per title key, so all versions of a series are one series.
    started: set[str] = field(default_factory=set)
    completed_series: dict[str, date] = field(default_factory=dict)   # finished for the first time this year
    finished_shows: dict[str, date] = field(default_factory=dict)
    listen_ms_per_day: dict[date, int] = field(default_factory=lambda: defaultdict(int))
    plays_per_day: dict[date, int] = field(default_factory=lambda: defaultdict(int))
    artists: Counter = field(default_factory=Counter)
    tracks: Counter = field(default_factory=Counter)


def _ranked(counter, limit=None) -> list:
    """Highest first; on a tie by name, so the order is fixed."""
    ranked = sorted(((k, v) for k, v in counter.items() if v > 0), key=lambda kv: (-kv[1], str(kv[0]).lower()))
    return ranked[:limit] if limit else ranked


def _busiest(per_day: dict) -> tuple[date, float] | None:
    """The day with the most; on a tie the first one."""
    return max(per_day.items(), key=lambda kv: (kv[1], -kv[0].toordinal()), default=None)


def _longest_streak(days) -> int:
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best, previous = max(best, run), day
    return best


class WrappedProjection:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._years: dict[int, _Year] = defaultdict(_Year)
        # A removed and re-added series gets a new series_id (Reading rule 6).
        # Wrapped counts all versions with the same title as one series: per key the series_ids, oldest first.
        self._series: dict[str, Title] = {}            # removed series too
        self._series_genres: dict[str, list[str]] = {}
        self._key: dict[str, str] = {}                 # series_id -> title key
        self._versions: dict[str, list[str]] = defaultdict(list)
        self._started_by: dict[str, str] = {}          # title key -> the series_id that started it
        self._just_added: SeriesStarted | None = None  # the previous Reading event, if that was SeriesStarted
        self._counted: dict[tuple[str, date], float] = {}  # chapters per (series_id, day), see chapters_read
        self._shows: dict[str, Title] = {}
        self._show_genres: dict[str, list[str]] = {}
        self._tracks: dict[str, tuple[str, tuple[str, ...]]] = {}

    # ---- Handling events ----

    def apply(self, event) -> None:
        if isinstance(event, (SeriesStarted, ProgressLogged, StatusChanged, GenresChanged, SeriesRemoved)):
            self._apply_reading(event)
            self._just_added = event if isinstance(event, SeriesStarted) else None
        elif isinstance(event, ShowAdded):
            self._shows[event.show_id] = Title(event.title, event.kind, event.cover)
        elif isinstance(event, ShowGenresChanged):
            self._show_genres[event.show_id] = list(event.genres)
        elif isinstance(event, ShowStatusChanged) and event.to_status is WatchStatus.COMPLETED:
            day = _day(event.at)
            self._years[day.year].finished_shows.setdefault(event.show_id, day)
        elif isinstance(event, TrackPlayed):
            day = _day(event.played_at)
            year = self._years[day.year]
            year.listen_ms_per_day[day] += listened_ms(event)
            year.plays_per_day[day] += 1
            year.artists.update(set(event.artists))
            year.tracks[event.track_id] += 1
            self._tracks[event.track_id] = (event.track, tuple(event.artists))

    def _apply_reading(self, event) -> None:
        day = _day(event.at)
        year = self._years[day.year]
        if isinstance(event, SeriesStarted):
            self._series[event.series_id] = Title(event.title, event.kind, event.cover)
            key = self._key[event.series_id] = title_key(event.title)
            self._versions[key].append(event.series_id)
            self._start(event.series_id, year)  # undone if the starting status is backlog or Completed
        elif isinstance(event, GenresChanged):
            self._series_genres[event.series_id] = list(event.genres)
        elif isinstance(event, ProgressLogged):
            day, chapters = chapters_read(event, self._counted)  # the same rule as ReadingActivity
            if chapters:
                year.chapters_per_day[day] += chapters
                year.chapters_per_series[event.series_id] += chapters
        elif isinstance(event, StatusChanged):
            if is_start_status(self._just_added, event):
                if event.to_status in (Status.PLAN_TO_READ, Status.COMPLETED):
                    # Added as backlog or as something you had already finished: not started, not finished.
                    # Only undo if this version started the series, not an earlier one.
                    key = self._key[event.series_id]
                    if self._started_by.get(key) == event.series_id:
                        del self._started_by[key]
                        self._years[_day(self._just_added.at).year].started.discard(key)
                return
            if event.from_status is Status.PLAN_TO_READ:
                self._start(event.series_id, year)  # out of the backlog: only started now
            if event.to_status is Status.COMPLETED and event.series_id in self._key:
                year.completed_series.setdefault(self._key[event.series_id], day)

    def _start(self, series_id: str, year: _Year) -> None:
        """A series starts once: in the year of the first version, not again after removing it."""
        key = self._key.get(series_id)
        if key is not None and key not in self._started_by:
            self._started_by[key] = series_id
            year.started.add(key)

    def _title(self, key: str) -> Title:
        """The newest version; without a cover, the cover of the newest older version that has one."""
        versions = [self._series[s] for s in reversed(self._versions[key])]
        cover = next((v.cover for v in versions if v.cover), None)
        return replace(versions[0], cover=cover)

    def _genres(self, key: str) -> list[str]:
        """The genres of the newest version that has genres (also for chapters of older versions)."""
        return next((g for s in reversed(self._versions[key]) if (g := self._series_genres.get(s))), [])

    # ---- Reading ----

    def years(self) -> list[int]:
        """Years with data, newest first."""
        return sorted((y for y in self._years if self.year(y).has_data), reverse=True)

    def year(self, number: int) -> YearReview:
        y = self._years.get(number) or _Year()

        chapters_per_title: Counter = Counter()
        for series_id, chapters in y.chapters_per_series.items():
            if series_id in self._key:
                chapters_per_title[self._key[series_id]] += chapters

        reading_genres: Counter = Counter()
        for key, chapters in chapters_per_title.items():
            genres = self._genres(key)
            for genre in genres:
                reading_genres[genre] += chapters / len(genres)

        watched = [self._shows[s] for s, _ in sorted(y.finished_shows.items(), key=lambda item: item[1])
                   if s in self._shows]
        watch_genres = Counter(g for s in y.finished_shows for g in self._show_genres.get(s, []))
        by_kind = {kind: sum(t.kind is kind for t in watched) for kind in WatchKind}

        listen_minutes = {day: ms / 60_000 for day, ms in y.listen_ms_per_day.items()}

        finished_days = list(y.completed_series.values()) + list(y.finished_shows.values())
        activity: dict[date, float] = defaultdict(float)
        for day, chapters in y.chapters_per_day.items():
            if chapters > 0:
                activity[day] += chapters
        for day in finished_days:
            activity[day] += 1
        for day, plays in y.plays_per_day.items():
            activity[day] += plays

        months = []
        for m in range(1, 13):
            chapters = sum(v for d, v in y.chapters_per_day.items() if d.month == m)
            finished = sum(d.month == m for d in finished_days)
            minutes = sum(v for d, v in listen_minutes.items() if d.month == m)
            score = sum(v for d, v in activity.items() if d.month == m)
            months.append(MonthRow(m, MONTH_NAMES[m - 1], chapters, finished, minutes, score))
        busiest_month = max((row for row in months if row.activity), key=lambda row: row.activity, default=None)

        return YearReview(
            year=number,
            chapters=sum(y.chapters_per_day.values()),
            top_series=sorted(((self._title(key), n) for key, n in chapters_per_title.items() if n > 0),
                              key=lambda tn: (-tn[1], tn[0].title.lower()))[:TOP],
            reading_genres=_ranked(reading_genres),
            series_started=len(y.started),
            series_completed=len(y.completed_series),
            longest_streak=_longest_streak(d for d, v in y.chapters_per_day.items() if v > 0),
            watched_by_kind=by_kind,
            watch_genres=_ranked(watch_genres),
            watched=watched,
            listen_minutes=sum(listen_minutes.values()),
            top_artists=_ranked(y.artists, TOP),
            top_tracks=[TrackTotal(*self._tracks[t], plays) for t, plays in _ranked(y.tracks, TOP)],
            busiest_listening_day=_busiest(listen_minutes),
            months=months,
            busiest_month=busiest_month,
            most_active_day=_busiest(activity),
        )
