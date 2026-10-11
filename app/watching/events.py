"""Events of Watching. Class names are unique across all domains, because the
event store only stores the class name (hence Show... instead of Series...)."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class WatchStatus(str, Enum):
    WATCHING = "watching"
    REWATCHING = "rewatching"  # something you have seen before, once more
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLAN_TO_WATCH = "plan_to_watch"  # backlog: not started yet


class WatchKind(str, Enum):
    MOVIE = "movie"
    SERIES = "series"
    ANIME = "anime"


# What statuses and kinds are called in the app (pages, toasts and Sunny's cards).
WATCH_STATUS_LABELS = {
    WatchStatus.WATCHING: "Watching",
    WatchStatus.REWATCHING: "Rewatching",
    WatchStatus.COMPLETED: "Completed",
    WatchStatus.ON_HOLD: "On hold",
    WatchStatus.DROPPED: "Dropped",
    WatchStatus.PLAN_TO_WATCH: "Plan to watch",
}
WATCH_KIND_LABELS = {WatchKind.SERIES: "Series", WatchKind.ANIME: "Anime", WatchKind.MOVIE: "Movie"}


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ShowAdded:
    show_id: str
    title: str
    kind: WatchKind
    status: WatchStatus  # the starting status; adding as Completed is history, not finishing
    at: datetime = field(default_factory=now)
    cover: str | None = None


@dataclass(frozen=True)
class ShowStatusChanged:
    show_id: str
    from_status: WatchStatus
    to_status: WatchStatus
    at: datetime = field(default_factory=now)


@dataclass(frozen=True)
class ShowGenresChanged:
    show_id: str
    genres: list[str]  # the complete new list
    at: datetime = field(default_factory=now)


@dataclass(frozen=True)
class ShowRemoved:
    show_id: str
    title: str
    at: datetime = field(default_factory=now)


EVENT_TYPES = [ShowAdded, ShowStatusChanged, ShowGenresChanged, ShowRemoved]
