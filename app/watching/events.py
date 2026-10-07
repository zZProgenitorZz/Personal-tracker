"""Events van Watching. Klassenamen zijn uniek over alle domeinen heen, want de
event store slaat alleen de klassenaam op (vandaar Show... in plaats van Series...)."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class WatchStatus(str, Enum):
    WATCHING = "watching"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"


class WatchKind(str, Enum):
    MOVIE = "movie"
    SERIES = "series"
    ANIME = "anime"


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ShowAdded:
    show_id: str
    title: str
    kind: WatchKind
    status: WatchStatus  # de beginstatus; toevoegen als Completed is geschiedenis, geen afronding
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
    genres: list[str]  # de volledige nieuwe lijst
    at: datetime = field(default_factory=now)


@dataclass(frozen=True)
class ShowRemoved:
    show_id: str
    title: str
    at: datetime = field(default_factory=now)
