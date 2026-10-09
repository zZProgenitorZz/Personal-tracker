"""Wrapped: een jaaroverzicht over Reading, Watching en Listening heen.

Alleen lezen. Deze projectie bouwt zich op uit de events die er al zijn, dus hij
werkt meteen met alle oude data en heeft geen eigen events of commands. Dagen,
maanden en jaren volgen de lokale tijdzone, net als de andere projecties.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from ..listening.events import TrackPlayed
from ..listening.projections import listened_ms
from ..reading.events import (
    GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged,
)
from ..watching.events import ShowAdded, ShowGenresChanged, ShowStatusChanged, WatchKind, WatchStatus

TOP = 5
# ReadingSeries.start legt een andere beginstatus vast als StatusChanged direct na SeriesStarted,
# in hetzelfde command (dus binnen milliseconden). Een statuswijziging die zo vlak na het
# toevoegen komt, is de beginstatus en geen echte wijziging (zie _is_start_status).
START_STATUS_WINDOW = timedelta(seconds=5)
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def title_key(title: str) -> str:
    """Dezelfde titel volgens Reading (find_by_title): hoofdletters en spaties eromheen tellen niet."""
    return title.strip().lower()


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
    activity: float  # hoofdstukken + afgeronde titels + gespeelde nummers


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
    # Alles samen
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
    # Begonnen en afgerond per titel-sleutel, zodat alle versies van een serie één serie zijn.
    started: set[str] = field(default_factory=set)
    completed_series: dict[str, date] = field(default_factory=dict)   # eerste keer afgerond dit jaar
    finished_shows: dict[str, date] = field(default_factory=dict)
    listen_ms_per_day: dict[date, int] = field(default_factory=lambda: defaultdict(int))
    plays_per_day: dict[date, int] = field(default_factory=lambda: defaultdict(int))
    artists: Counter = field(default_factory=Counter)
    tracks: Counter = field(default_factory=Counter)


def _ranked(counter, limit=None) -> list:
    """Hoogste eerst; bij gelijkspel op naam, zodat de volgorde vast is."""
    ranked = sorted(((k, v) for k, v in counter.items() if v > 0), key=lambda kv: (-kv[1], str(kv[0]).lower()))
    return ranked[:limit] if limit else ranked


def _busiest(per_day: dict) -> tuple[date, float] | None:
    """De dag met het meeste; bij gelijkspel de eerste."""
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
        # Een verwijderde en opnieuw toegevoegde serie krijgt een nieuwe series_id (Reading regel 6).
        # Wrapped telt alle versies met dezelfde titel als één serie: per sleutel de series_id's, oudste eerst.
        self._series: dict[str, Title] = {}            # ook van verwijderde series
        self._series_genres: dict[str, list[str]] = {}
        self._key: dict[str, str] = {}                 # series_id -> titel-sleutel
        self._versions: dict[str, list[str]] = defaultdict(list)
        self._started_by: dict[str, str] = {}          # titel-sleutel -> de series_id die hem liet beginnen
        self._just_added: SeriesStarted | None = None  # het vorige Reading-event, als dat SeriesStarted was
        self._shows: dict[str, Title] = {}
        self._show_genres: dict[str, list[str]] = {}
        self._tracks: dict[str, tuple[str, tuple[str, ...]]] = {}

    # ---- Events verwerken ----

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
            self._start(event.series_id, year)  # wordt teruggedraaid als de beginstatus backlog of Completed is
        elif isinstance(event, GenresChanged):
            self._series_genres[event.series_id] = list(event.genres)
        elif isinstance(event, ProgressLogged):
            chapters = event.chapter - event.previous_chapter
            if chapters > 0:  # dezelfde regel als ReadingActivity
                year.chapters_per_day[day] += chapters
                year.chapters_per_series[event.series_id] += chapters
        elif isinstance(event, StatusChanged):
            if self._is_start_status(event):
                if event.to_status in (Status.PLAN_TO_READ, Status.COMPLETED):
                    # Toegevoegd als backlog of als iets wat je al uit had: niet begonnen, niet afgerond.
                    # Alleen terugdraaien als déze versie de serie liet beginnen, niet een eerdere.
                    key = self._key[event.series_id]
                    if self._started_by.get(key) == event.series_id:
                        del self._started_by[key]
                        self._years[_day(self._just_added.at).year].started.discard(key)
                return
            if event.from_status is Status.PLAN_TO_READ:
                self._start(event.series_id, year)  # uit de backlog: nu pas begonnen
            if event.to_status is Status.COMPLETED and event.series_id in self._key:
                year.completed_series.setdefault(self._key[event.series_id], day)

    def _is_start_status(self, event: StatusChanged) -> bool:
        """De beginstatus uit ReadingSeries.start: volgt direct op de SeriesStarted van dezelfde serie
        (geen ander Reading-event ertussen) en binnen een paar seconden. Listening-plays van de sync
        mogen ertussen zitten; die tellen voor deze regel niet mee."""
        added = self._just_added
        return (added is not None and added.series_id == event.series_id
                and event.from_status is Status.READING
                and abs(event.at - added.at) <= START_STATUS_WINDOW)

    def _start(self, series_id: str, year: _Year) -> None:
        """Een serie begint één keer: in het jaar van de eerste versie, niet opnieuw na verwijderen."""
        key = self._key.get(series_id)
        if key is not None and key not in self._started_by:
            self._started_by[key] = series_id
            year.started.add(key)

    def _title(self, key: str) -> Title:
        """De nieuwste versie; zonder cover de cover van de nieuwste oudere versie die er wel een heeft."""
        versions = [self._series[s] for s in reversed(self._versions[key])]
        cover = next((v.cover for v in versions if v.cover), None)
        return replace(versions[0], cover=cover)

    def _genres(self, key: str) -> list[str]:
        """De genres van de nieuwste versie die genres heeft (ook voor hoofdstukken van oudere versies)."""
        return next((g for s in reversed(self._versions[key]) if (g := self._series_genres.get(s))), [])

    # ---- Lezen ----

    def years(self) -> list[int]:
        """Jaren met data, nieuwste eerst."""
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
            top_series=sorted(((self._title(key), n) for key, n in chapters_per_title.items()),
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
