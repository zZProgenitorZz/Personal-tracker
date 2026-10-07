from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from .events import ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged, WatchKind, WatchStatus


@dataclass
class WatchEntry:
    show_id: str
    title: str
    kind: WatchKind
    status: WatchStatus
    added_at: datetime
    updated_at: datetime
    cover: str | None = None
    genres: list[str] = field(default_factory=list)


class WatchlistProjection:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._entries: dict[str, WatchEntry] = {}

    def apply(self, event) -> None:
        if isinstance(event, ShowAdded):
            self._entries[event.show_id] = WatchEntry(
                event.show_id, event.title, event.kind, event.status, event.at, event.at, event.cover)
        elif isinstance(event, ShowStatusChanged):
            entry = self._entries[event.show_id]
            entry.status, entry.updated_at = event.to_status, event.at
        elif isinstance(event, ShowGenresChanged):
            self._entries[event.show_id].genres = list(event.genres)
        elif isinstance(event, ShowRemoved):
            del self._entries[event.show_id]  # de titel is daarmee weer vrij

    def all(self) -> list[WatchEntry]:
        return sorted(self._entries.values(), key=lambda e: e.title.lower())

    def by_status(self, status: WatchStatus) -> list[WatchEntry]:
        return [e for e in self.all() if e.status is status]

    def currently_watching(self) -> list[WatchEntry]:
        return sorted(self.by_status(WatchStatus.WATCHING), key=lambda e: e.updated_at, reverse=True)

    def find(self, title: str, kind: WatchKind) -> WatchEntry | None:
        wanted = title.strip().lower()
        return next((e for e in self._entries.values() if e.title.lower() == wanted and e.kind is kind), None)

    def get(self, show_id: str) -> WatchEntry | None:
        return self._entries.get(show_id)


class WatchActivityProjection:
    """Afgerond per dag: alleen wanneer je iets op Completed zet, niet bij het toevoegen."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._per_day: dict[date, int] = defaultdict(int)

    def apply(self, event) -> None:
        if isinstance(event, ShowStatusChanged) and event.to_status is WatchStatus.COMPLETED:
            self._per_day[event.at.astimezone().date()] += 1

    def per_day(self) -> dict[date, int]:
        return dict(sorted(self._per_day.items()))
