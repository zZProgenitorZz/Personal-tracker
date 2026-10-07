from ..domain import DomainError
from .events import ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged, WatchKind, WatchStatus
from .genres import WATCH_GENRES


class WatchItem:
    """Eén film, serie of anime, opgebouwd uit zijn events."""

    def __init__(self, events: list):
        self.show_id = None
        self.title = None
        self.status = None
        self.removed = False
        self.genres: list[str] = []
        for event in events:
            self._apply(event)

    @property
    def exists(self) -> bool:
        return self.show_id is not None

    # ---- Toestand opbouwen uit events ----

    def _apply(self, event) -> None:
        if isinstance(event, ShowAdded):
            self.show_id, self.title, self.status = event.show_id, event.title, event.status
        elif isinstance(event, ShowStatusChanged):
            self.status = event.to_status
        elif isinstance(event, ShowGenresChanged):
            self.genres = list(event.genres)
        elif isinstance(event, ShowRemoved):
            self.removed = True

    # ---- Beslissingen: regels checken, nieuwe events teruggeven ----

    def add(self, show_id: str, title: str, kind: WatchKind, status: WatchStatus = WatchStatus.WATCHING,
            cover: str | None = None, genres=()) -> list:
        if self.exists:
            raise DomainError("Deze titel bestaat al")
        events = self._record(ShowAdded(show_id, title, kind, status, cover=cover))
        return events + self.set_genres(genres)

    def change_status(self, new_status: WatchStatus) -> list:
        self._require_active()
        if new_status is self.status:
            raise DomainError(f"Status is al {new_status.value}")
        return self._record(ShowStatusChanged(self.show_id, self.status, new_status))

    def set_genres(self, genres) -> list:
        self._require_active()
        try:
            chosen = WATCH_GENRES.validated(genres)
        except ValueError as exc:
            raise DomainError(f"Onbekend genre: {exc}") from exc
        if chosen == self.genres:
            return []  # niets veranderd, dus ook geen event
        return self._record(ShowGenresChanged(self.show_id, chosen))

    def remove(self) -> list:
        self._require_active()
        return self._record(ShowRemoved(self.show_id, self.title))

    def _require_active(self) -> None:
        if not self.exists:
            raise DomainError("Onbekende titel")
        if self.removed:
            raise DomainError(f"'{self.title}' is verwijderd")

    def _record(self, event) -> list:
        self._apply(event)
        return [event]
