"""Events van Watching. Klassenamen zijn uniek over alle domeinen heen, want de
event store slaat alleen de klassenaam op (vandaar Show... in plaats van Series...)."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class WatchStatus(str, Enum):
    WATCHING = "watching"
    REWATCHING = "rewatching"  # iets wat je al zag, nog een keer
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLAN_TO_WATCH = "plan_to_watch"  # backlog: nog niet begonnen


class WatchKind(str, Enum):
    MOVIE = "movie"
    SERIES = "series"
    ANIME = "anime"


# Hoe statussen en soorten in de app heten (pagina's, toasts en Sunny's kaarten).
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
