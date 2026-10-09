from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Status(str, Enum):
    READING = "reading"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    DROPPED = "dropped"
    PLAN_TO_READ = "plan_to_read"  # backlog: nog niet begonnen


class Kind(str, Enum):
    NOVEL = "novel"
    MANHWA = "manhwa"

def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SeriesStarted:
    series_id: str
    title: str
    kind: Kind
    source: str
    start_chapter: int
    at: datetime = field(default_factory=now)
    # Bestandsnaam in de covermap. Oudere events hebben dit veld niet, vandaar de default.
    cover: str | None = None


@dataclass(frozen=True)
class ProgressLogged:
    series_id: str
    chapter: int
    previous_chapter: int
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
    genres: list[str]  # de volledige nieuwe lijst, niet een toevoeging
    at: datetime = field(default_factory=now)


@dataclass(frozen=True)
class SeriesRemoved:
    series_id: str
    title: str
    at: datetime = field(default_factory=now)