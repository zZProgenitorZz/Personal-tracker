from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .events import Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged


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


class LibraryProjection:
    def __init__(self):
        self._entries: dict[str, LibraryEntry] = {}
        # Verwijderde series blijven bewaard, zodat hun titel bezet blijft.
        self._removed: dict[str, LibraryEntry] = {}

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
        elif isinstance(event, SeriesRemoved):
            self._removed[event.series_id] = self._entries.pop(event.series_id)

    def all(self) -> list[LibraryEntry]:
        return sorted(self._entries.values(), key=lambda e: e.title.lower())

    def by_status(self, status: Status) -> list[LibraryEntry]:
        return [e for e in self.all() if e.status is status]

    def currently_reading(self) -> list[LibraryEntry]:
        reading = self.by_status(Status.READING)
        return sorted(reading, key=lambda e: e.updated_at, reverse=True)

    def find_by_title(self, title: str) -> LibraryEntry | None:
        wanted = title.strip().lower()
        everything = [*self._entries.values(), *self._removed.values()]
        return next((e for e in everything if e.title.lower() == wanted), None)

    def get(self, series_id: str) -> LibraryEntry | None:
        return self._entries.get(series_id)


class ReadingActivityProjection:
    def __init__(self):
        self._per_day: dict[date, float] = defaultdict(float)

    def apply(self, event) -> None:
        if isinstance(event, ProgressLogged):
            chapters_read = event.chapter - event.previous_chapter
            if chapters_read > 0:
                self._per_day[event.at.astimezone().date()] += chapters_read

    def per_day(self) -> dict[date, float]:
        return dict(sorted(self._per_day.items()))

    def total_chapters(self) -> float:
        return sum(self._per_day.values())

    def streak(self, today: date) -> int:
        """Aantal dagen op rij met gelezen hoofdstukken. Vandaag telt pas mee
        als er gelezen is, maar breekt de reeks ook niet zolang de dag nog loopt."""
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