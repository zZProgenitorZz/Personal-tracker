from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..domain import title_key
from .events import (
    GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged, is_start_status,
)


def chapters_read(event: ProgressLogged, counted: dict[tuple[str, date], float]) -> tuple[date, float]:
    """The one rule for chapters read, shared by ReadingActivity, Wrapped and the reading history.

    Forward progress counts. A step down on the same local day is a typo correction: it takes the
    difference back from that series' chapters of that day (never below 0). A step down on a later
    day is a reread and counts 0. `counted` keeps the chapters per (series_id, day) and is updated.
    Returns the local day and the change for that day (negative for a correction)."""
    day = event.at.astimezone().date()
    step = event.chapter - event.previous_chapter
    change = step if step > 0 else -min(-step, counted.get((event.series_id, day), 0))
    if change:
        counted[(event.series_id, day)] = counted.get((event.series_id, day), 0) + change
    return day, change


@dataclass
class LibraryEntry:
    series_id: str
    title: str
    kind: Kind
    source: str
    status: Status
    current_chapter: float
    updated_at: datetime
    cover: str | None = None
    genres: list[str] = field(default_factory=list)


class LibraryProjection:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """Starts empty, for example to rebuild after restoring a backup."""
        self._entries: dict[str, LibraryEntry] = {}

    def apply(self, event) -> None:
        if isinstance(event, SeriesStarted):
            self._entries[event.series_id] = LibraryEntry(
                event.series_id, event.title, event.kind, event.source,
                Status.READING, event.start_chapter, event.at, event.cover,
            )
        elif isinstance(event, ProgressLogged):
            entry = self._entries[event.series_id]
            entry.current_chapter = event.chapter
            entry.updated_at = event.at
        elif isinstance(event, StatusChanged):
            entry = self._entries[event.series_id]
            entry.status = event.to_status
            entry.updated_at = event.at
        elif isinstance(event, GenresChanged):
            self._entries[event.series_id].genres = list(event.genres)
        elif isinstance(event, SeriesRemoved):
            del self._entries[event.series_id]  # which frees up the title again

    def all(self) -> list[LibraryEntry]:
        return sorted(self._entries.values(), key=lambda e: e.title.lower())

    def by_status(self, status: Status) -> list[LibraryEntry]:
        return [e for e in self.all() if e.status is status]

    def currently_reading(self) -> list[LibraryEntry]:
        reading = self.by_status(Status.READING)
        return sorted(reading, key=lambda e: e.updated_at, reverse=True)

    def stale(self, now: datetime, days: int) -> list[LibraryEntry]:
        """Gathering dust: on Reading, but no progress or status change for longer than `days` days.
        Quiet the longest first."""
        limit = now - timedelta(days=days)
        return sorted((e for e in self.by_status(Status.READING) if e.updated_at < limit), key=lambda e: e.updated_at)

    def find_by_title(self, title: str) -> LibraryEntry | None:
        wanted = title.strip().lower()
        return next((e for e in self._entries.values() if e.title.lower() == wanted), None)

    def get(self, series_id: str) -> LibraryEntry | None:
        return self._entries.get(series_id)


class ReadingActivityProjection:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._per_day: dict[date, float] = defaultdict(float)
        self._counted: dict[tuple[str, date], float] = {}

    def apply(self, event) -> None:
        if isinstance(event, ProgressLogged):
            day, change = chapters_read(event, self._counted)
            if change:
                self._per_day[day] += change

    def per_day(self) -> dict[date, float]:
        return {day: chapters for day, chapters in sorted(self._per_day.items()) if chapters > 0}

    def total_chapters(self) -> float:
        return sum(self._per_day.values())

    def streak(self, today: date) -> int:
        """Number of days in a row with chapters read. Today only counts
        once you have read, but it doesn't break the streak while the day is still running."""
        day = today if self._per_day.get(today) else today - timedelta(days=1)
        days = 0
        while self._per_day.get(day, 0) > 0:
            days += 1
            day -= timedelta(days=1)
        return days

    def per_week(self) -> dict[str, float]:
        weeks = defaultdict(float)
        for day, chapters in self._per_day.items():
            year, week, _ = day.isocalendar()
            weeks[f"{year}-W{week:02d}"] += chapters
        return dict(sorted(weeks.items()))


# ---- History: what happened when, removed series included ----

StatusChange = tuple[date, Status, Status]  # (local day, from, to)


@dataclass
class _ReadingTitle:
    """All versions of one series (a re-added series gets a new series_id), by title key."""
    first_id: str  # the first version's series_id: its starting status is the one shown
    title: str
    kind: Kind
    added: date
    start_status: Status = Status.READING
    changes: list[StatusChange] = field(default_factory=list)
    chapters: dict[date, float] = field(default_factory=lambda: defaultdict(float))
    logged: dict[date, tuple[float, float]] = field(default_factory=dict)  # day -> (first from, last to)


@dataclass(frozen=True)
class ReadingPeriodRow:
    key: str
    title: str
    kind: Kind
    chapters: float
    from_chapter: float | None
    to_chapter: float | None
    changes: list[StatusChange]


@dataclass(frozen=True)
class ReadingPeriod:
    rows: list[ReadingPeriodRow]
    chapters: float
    days_read: int

    @property
    def completed(self) -> int:
        return sum(to is Status.COMPLETED for row in self.rows for _, _, to in row.changes)


@dataclass(frozen=True)
class ReadingTimeline:
    title: str
    kind: Kind
    added: date
    start_status: Status
    changes: list[StatusChange]
    times_completed: int
    first_read: date | None
    last_read: date | None
    chapters: float


class ReadingHistoryProjection:
    """Read only: status changes and chapters read per day for every series that was ever added.
    Days follow the local time zone; chapters follow chapters_read, so the numbers match Reading."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._titles: dict[str, _ReadingTitle] = {}
        self._key: dict[str, str] = {}                 # series_id -> title key (removed series too)
        self._counted: dict[tuple[str, date], float] = {}
        self._just_added: SeriesStarted | None = None  # the previous Reading event, if that was SeriesStarted

    def apply(self, event) -> None:
        if isinstance(event, SeriesStarted):
            self._started(event)
        elif isinstance(event, ProgressLogged):
            self._progress(event)
        elif isinstance(event, StatusChanged) and event.series_id in self._key:
            title = self._titles[self._key[event.series_id]]
            if not is_start_status(self._just_added, event):
                title.changes.append((event.at.astimezone().date(), event.from_status, event.to_status))
            elif event.series_id == title.first_id:
                title.start_status = event.to_status
        if isinstance(event, (SeriesStarted, ProgressLogged, StatusChanged, GenresChanged, SeriesRemoved)):
            self._just_added = event if isinstance(event, SeriesStarted) else None

    def _started(self, event: SeriesStarted) -> None:
        key = self._key[event.series_id] = title_key(event.title)
        if key in self._titles:  # a newer version: its title and kind are shown
            self._titles[key].title, self._titles[key].kind = event.title, event.kind
        else:
            self._titles[key] = _ReadingTitle(event.series_id, event.title, event.kind, event.at.astimezone().date())

    def _progress(self, event: ProgressLogged) -> None:
        if event.series_id not in self._key:
            return
        title = self._titles[self._key[event.series_id]]
        day, change = chapters_read(event, self._counted)
        title.chapters[day] += change
        first, _ = title.logged.get(day, (event.previous_chapter, None))
        title.logged[day] = (first, event.chapter)

    def key_of(self, series_id: str) -> str | None:
        return self._key.get(series_id)

    def between(self, start: date, end: date) -> ReadingPeriod:
        """Every series with progress or a status change from `start` up to and including `end`,
        most chapters first."""
        rows, per_day = [], defaultdict(float)
        for key, title in self._titles.items():
            days = sorted(d for d in title.logged if start <= d <= end)
            changes = [c for c in title.changes if start <= c[0] <= end]
            if not days and not changes:
                continue
            for day in days:
                per_day[day] += title.chapters[day]
            rows.append(ReadingPeriodRow(
                key, title.title, title.kind, sum(title.chapters[d] for d in days),
                title.logged[days[0]][0] if days else None, title.logged[days[-1]][1] if days else None, changes))
        rows.sort(key=lambda r: (-r.chapters, r.title.lower()))
        return ReadingPeriod(rows, sum(per_day.values()), sum(1 for v in per_day.values() if v > 0))

    def timeline(self, key: str) -> ReadingTimeline | None:
        title = self._titles.get(key)
        if title is None:
            return None
        read = sorted(d for d, chapters in title.chapters.items() if chapters > 0)
        return ReadingTimeline(
            title.title, title.kind, title.added, title.start_status, list(title.changes),
            sum(to is Status.COMPLETED for _, _, to in title.changes),
            read[0] if read else None, read[-1] if read else None, sum(title.chapters.values()))
