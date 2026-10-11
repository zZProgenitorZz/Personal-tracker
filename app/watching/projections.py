from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .events import ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged, WatchKind, WatchStatus


WATCHING_NOW = (WatchStatus.WATCHING, WatchStatus.REWATCHING)  # counts as "watching"


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
            del self._entries[event.show_id]  # which frees up the title again

    def all(self) -> list[WatchEntry]:
        return sorted(self._entries.values(), key=lambda e: e.title.lower())

    def by_status(self, status: WatchStatus) -> list[WatchEntry]:
        return [e for e in self.all() if e.status is status]

    def currently_watching(self) -> list[WatchEntry]:
        watching = [e for e in self.all() if e.status in WATCHING_NOW]
        return sorted(watching, key=lambda e: e.updated_at, reverse=True)

    def stale(self, now: datetime, days: int) -> list[WatchEntry]:
        """Gathering dust: on Watching, but not added or changed status for longer than `days` days.
        Quiet the longest first. (Changing genres doesn't count.)"""
        limit = now - timedelta(days=days)
        return sorted((e for e in self.all() if e.status in WATCHING_NOW and e.updated_at < limit),
                      key=lambda e: e.updated_at)

    def find(self, title: str, kind: WatchKind) -> WatchEntry | None:
        wanted = title.strip().lower()
        return next((e for e in self._entries.values() if e.title.lower() == wanted and e.kind is kind), None)

    def get(self, show_id: str) -> WatchEntry | None:
        return self._entries.get(show_id)


class WatchActivityProjection:
    """Finished per day: only when you set something to Completed, not when adding it."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._per_day: dict[date, int] = defaultdict(int)

    def apply(self, event) -> None:
        if isinstance(event, ShowStatusChanged) and event.to_status is WatchStatus.COMPLETED:
            self._per_day[event.at.astimezone().date()] += 1

    def per_day(self) -> dict[date, int]:
        return dict(sorted(self._per_day.items()))
