"""Events of Listening. Class names are unique across all domains, because the
event store only stores the class name."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TrackPlayed:
    played_at: datetime        # when you listened to the track (UTC)
    track_id: str              # Spotify URI, e.g. spotify:track:...
    track: str
    artists: list[str]
    album: str
    album_id: str
    duration_ms: int           # length of the track
    ms_played: int | None = None  # how long you really listened; the API doesn't give this, an export does
    source: str = "api"        # "api" (live sync) or "export" (export_import.py)
    at: datetime = field(default_factory=now)  # when Progen recorded it


EVENT_TYPES = [TrackPlayed]
