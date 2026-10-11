from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..domain import title_key
from .events import ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged, WatchKind, WatchStatus


WATCHING_NOW = (WatchStatus.WATCHING, WatchStatus.REWATCHING)  # counts as "watching"
# Gathering dust: a title on Watching that hasn't been added or changed status in this many days.
# Looser than for Reading, because Watching doesn't track episodes: a weaker signal.
STALE_WATCHING_DAYS = 30


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


# ---- History: what happened when, removed shows included ----

WatchKey = tuple[str, WatchKind]  # (title key, kind): a film and a series may share a title
WatchChange = tuple[date, WatchStatus, WatchStatus]  # (local day, from, to)


@dataclass
class _WatchTitle:
    """All versions of one show (re-added after removing), by title key and kind."""
    title: str
    kind: WatchKind
    added: date
    start_status: WatchStatus
    changes: list[WatchChange] = field(default_factory=list)
    # Every status with the day it started; None = removed. For "what was on in a period".
    statuses: list[tuple[date, WatchStatus | None]] = field(default_factory=list)


@dataclass(frozen=True)
class WatchPeriodRow:
    key: WatchKey
    title: str
    kind: WatchKind
    finished_on: date | None  # None: watched in the period, not finished in it
    rewatch: bool


@dataclass(frozen=True)
class WatchTimeline:
    title: str
    kind: WatchKind
    added: date
    start_status: WatchStatus
    changes: list[WatchChange]
    completed: list[date | None]  # None: added as already completed, date unknown
    times_rewatched: int

    @property
    def times_completed(self) -> int:
        return len(self.completed)


class WatchHistoryProjection:
    """Read only: every status of every show that was ever added, with its local day."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._titles: dict[WatchKey, _WatchTitle] = {}
        self._key: dict[str, WatchKey] = {}  # show_id -> key (removed shows too)

    def apply(self, event) -> None:
        if isinstance(event, ShowAdded):
            key = self._key[event.show_id] = (title_key(event.title), event.kind)
            day = event.at.astimezone().date()
            if key in self._titles:  # a newer version: its title is shown
                self._titles[key].title = event.title
            else:
                self._titles[key] = _WatchTitle(event.title, event.kind, day, event.status)
            self._titles[key].statuses.append((day, event.status))
        elif isinstance(event, ShowStatusChanged) and event.show_id in self._key:
            title, day = self._titles[self._key[event.show_id]], event.at.astimezone().date()
            title.changes.append((day, event.from_status, event.to_status))
            title.statuses.append((day, event.to_status))
        elif isinstance(event, ShowRemoved) and event.show_id in self._key:
            self._titles[self._key[event.show_id]].statuses.append((event.at.astimezone().date(), None))

    def key_of(self, show_id: str) -> WatchKey | None:
        return self._key.get(show_id)

    def between(self, start: date, end: date) -> list[WatchPeriodRow]:
        """Shows finished from `start` up to and including `end` (with the day), and shows that were on
        Watching or Rewatching at some moment in that period without being finished in it."""
        rows = []
        for key, title in self._titles.items():
            finished = [(d, frm) for d, frm, to in title.changes if to is WatchStatus.COMPLETED and start <= d <= end]
            rows += [WatchPeriodRow(key, title.title, title.kind, d, frm is WatchStatus.REWATCHING)
                     for d, frm in finished]
            watched = _watched_between(title.statuses, start, end)
            if watched and not finished:
                rows.append(WatchPeriodRow(key, title.title, title.kind, None, WatchStatus.REWATCHING in watched))
        return sorted(rows, key=lambda r: (r.finished_on is None, r.finished_on or date.min, r.title.lower()))

    def timeline(self, key: WatchKey) -> WatchTimeline | None:
        title = self._titles.get(key)
        if title is None:
            return None
        completed = [None] if title.start_status is WatchStatus.COMPLETED else []
        completed += [d for d, _, to in title.changes if to is WatchStatus.COMPLETED]
        return WatchTimeline(title.title, title.kind, title.added, title.start_status, list(title.changes),
                             completed, sum(to is WatchStatus.REWATCHING for _, _, to in title.changes))


def _watched_between(statuses: list[tuple[date, WatchStatus | None]], start: date, end: date) -> set[WatchStatus]:
    """The watching statuses whose period overlaps start..end. A status lasts from its day up to and
    including the day of the next one (it was on for part of that day)."""
    found = set()
    for i, (since, status) in enumerate(statuses):
        until = statuses[i + 1][0] if i + 1 < len(statuses) else None
        if status in WATCHING_NOW and since <= end and (until is None or until >= start):
            found.add(status)
    return found
