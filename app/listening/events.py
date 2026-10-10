"""Events van Listening. Klassenamen zijn uniek over alle domeinen heen, want de
event store slaat alleen de klassenaam op."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TrackPlayed:
    played_at: datetime        # wanneer je het nummer luisterde (UTC)
    track_id: str              # Spotify-URI, bv. spotify:track:...
    track: str
    artists: list[str]
    album: str
    album_id: str
    duration_ms: int           # lengte van het nummer
    ms_played: int | None = None  # hoe lang je echt luisterde; de API geeft dit niet, een export wel
    source: str = "api"        # "api" (live sync) of "export" (import_export.py)
    at: datetime = field(default_factory=now)  # wanneer Progen het vastlegde
