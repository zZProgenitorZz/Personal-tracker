from .events import GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged
from .genres import validated


class DomainError(Exception):
    """Een verzoek dat de regels van het domein overtreedt."""


class ReadingSeries:
    def __init__(self, events: list):
        self.series_id = None
        self.title = None
        self.status = None
        self.current_chapter = None
        self.removed = False
        self.genres: list[str] = []
        for event in events:
            self._apply(event)

    @property
    def exists(self) -> bool:
        return self.series_id is not None

    # ---- Toestand opbouwen uit events ----

    def _apply(self, event) -> None:
        if isinstance(event, SeriesStarted):
            self.series_id = event.series_id
            self.title = event.title
            self.status = Status.READING
            self.current_chapter = event.start_chapter
        elif isinstance(event, ProgressLogged):
            self.current_chapter = event.chapter
        elif isinstance(event, StatusChanged):
            self.status = event.to_status
        elif isinstance(event, SeriesRemoved):
            self.removed = True
        elif isinstance(event, GenresChanged):
            self.genres = list(event.genres)

    # ---- Beslissingen: regels checken, nieuwe events teruggeven ----

    def start(self, series_id: str, title: str, kind: Kind, source: str, start_chapter: float,
              cover: str | None = None, status: Status = Status.READING, genres=()) -> list:
        if self.exists:
            raise DomainError("Deze serie bestaat al")
        events = self._record(SeriesStarted(series_id, title, kind, source, start_chapter, cover=cover))
        # Een serie begint altijd als Reading; een andere beginstatus is een gewone statuswijziging.
        if status is not Status.READING:
            events += self._record(StatusChanged(series_id, Status.READING, status))
        events += self.set_genres(genres)
        return events

    def log_progress(self, chapter: float) -> list:
        self._require_active()
        if chapter < 0:
            raise DomainError("Hoofdstuk kan niet negatief zijn")
        events = []
        if self.status is not Status.READING:
            events += self._record(StatusChanged(self.series_id, self.status, Status.READING))
        events += self._record(ProgressLogged(self.series_id, chapter, self.current_chapter))
        return events

    def change_status(self, new_status: Status) -> list:
        self._require_active()
        if new_status is self.status:
            raise DomainError(f"Status is al {new_status.value}")
        return self._record(StatusChanged(self.series_id, self.status, new_status))

    def set_genres(self, genres) -> list:
        self._require_active()
        try:
            chosen = validated(genres)
        except ValueError as exc:
            raise DomainError(f"Onbekend genre: {exc}") from exc
        if chosen == self.genres:
            return []  # niets veranderd, dus ook geen event
        return self._record(GenresChanged(self.series_id, chosen))

    def remove(self) -> list:
        self._require_active()
        return self._record(SeriesRemoved(self.series_id, self.title))

    def _require_active(self) -> None:
        if not self.exists:
            raise DomainError("Onbekende serie")
        if self.removed:
            raise DomainError(f"'{self.title}' is verwijderd")

    def _record(self, event) -> list:
        self._apply(event)
        return [event]