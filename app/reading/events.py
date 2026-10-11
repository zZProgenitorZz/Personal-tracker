from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Status(str, Enum):
    READING = "reading"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    DROPPED = "dropped"
    PLAN_TO_READ = "plan_to_read"  # backlog: not started yet


class Kind(str, Enum):
    NOVEL = "novel"
    MANHWA = "manhwa"


# What statuses and kinds are called in the app (pages, toasts and Sunny's cards).
STATUS_LABELS = {
    Status.READING: "Reading",
    Status.ON_HOLD: "On hold",
    Status.COMPLETED: "Completed",
    Status.DROPPED: "Dropped",
    Status.PLAN_TO_READ: "Plan to read",
}
KIND_LABELS = {Kind.MANHWA: "Manhwa", Kind.NOVEL: "Novel"}


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SeriesStarted:
    series_id: str
    title: str
    kind: Kind
    source: str
    start_chapter: float
    at: datetime = field(default_factory=now)
    # File name in the covers folder. Older events don't have this field, hence the default.
    cover: str | None = None


@dataclass(frozen=True)
class ProgressLogged:
    series_id: str
    chapter: float
    previous_chapter: float
    at: datetime = field(default_factory=now)


@dataclass(frozen=True)
class StatusChanged:
    series_id: str
    from_status: Status
    to_status: Status
    at: datetime = field(default_factory=now)


@dataclass(frozen=True)
class GenresChanged:
    series_id: str
    genres: list[str]  # the complete new list, not an addition
    at: datetime = field(default_factory=now)


@dataclass(frozen=True)
class SeriesRemoved:
    series_id: str
    title: str
    at: datetime = field(default_factory=now)


EVENT_TYPES = [SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved, GenresChanged]
