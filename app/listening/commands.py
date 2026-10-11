from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ..eventstore import EventStore
from .aggregate import Play


# ---- Commands: what you want to happen ----

@dataclass(frozen=True)
class RecordPlay:
    played_at: datetime
    track_id: str
    track: str
    artists: list[str]
    album: str
    album_id: str
    duration_ms: int
    ms_played: int | None = None
    source: str = "api"


def as_utc(moment: datetime) -> datetime:
    """A time without a time zone counts as UTC (that is how Spotify delivers them too)."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def stream_id(played_at: datetime) -> str:
    """Each moment is its own stream, so the same play is never stored twice."""
    return f"play-{as_utc(played_at).isoformat()}"


# ---- The handler: load, decide, save ----

class ListeningCommandHandler:
    def __init__(self, store: EventStore):
        self._store = store

    def handle(self, command) -> list:
        """Returns the stored events; an empty list means: already known, nothing stored."""
        if not isinstance(command, RecordPlay):
            raise TypeError(f"Onbekend command: {type(command).__name__}")
        command = replace(command, played_at=as_utc(command.played_at))
        stream = stream_id(command.played_at)
        events = Play(self._store.load_stream(stream)).record(command)
        if events:
            self._store.append(stream, events)
        return events
