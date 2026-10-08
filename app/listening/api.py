from datetime import date, datetime

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .commands import ListeningCommandHandler, RecordPlay, stream_id
from .projections import (
    ListeningActivityProjection, RecentlyPlayedProjection, TopArtistsProjection, TopTracksProjection,
)


# ---- Wat de API binnen verwacht ----

class PlayBody(BaseModel):
    played_at: datetime  # zonder tijdzone geldt UTC
    track_id: str
    track: str
    artists: list[str] = []
    album: str = ""
    album_id: str = ""
    duration_ms: int
    ms_played: int | None = None
    source: str = "api"


MONTH = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM; standaard deze maand")


def _year_month(month: str | None) -> tuple[int, int]:
    if month is None:
        today = date.today()
        return today.year, today.month
    year, number = month.split("-")
    return int(year), int(number)


# ---- De endpoints ----

def create_listening_router(
    handler: ListeningCommandHandler,
    recent: RecentlyPlayedProjection,
    activity: ListeningActivityProjection,
    top_artists: TopArtistsProjection,
    top_tracks: TopTracksProjection,
) -> APIRouter:
    router = APIRouter(prefix="/listening", tags=["listening"])

    # Lezen: vragen aan de read models

    @router.get("/recent")
    def get_recent():
        return recent.recent()

    @router.get("/activity")
    def get_activity():
        return {
            "minutes_per_day": {str(day): round(m, 2) for day, m in activity.minutes_per_day().items()},
            "minutes_per_week": {week: round(m, 2) for week, m in activity.minutes_per_week().items()},
        }

    @router.get("/top-artists")
    def get_top_artists(month: str | None = MONTH, limit: int = 10):
        return [{"artist": a, "plays": n} for a, n in top_artists.top(*_year_month(month), limit=limit)]

    @router.get("/top-tracks")
    def get_top_tracks(month: str | None = MONTH, limit: int = 10):
        return top_tracks.top(*_year_month(month), limit=limit)

    # Schrijven: commands naar de handler

    @router.post("/plays", status_code=201)
    def record_play(body: PlayBody):
        """201 als de play is opgeslagen, 200 als dit moment al bekend was (overgeslagen)."""
        command = RecordPlay(body.played_at, body.track_id, body.track, body.artists, body.album,
                             body.album_id, body.duration_ms, body.ms_played, body.source)
        stored = bool(handler.handle(command))
        result = {"stored": stored, "stream_id": stream_id(body.played_at)}
        return result if stored else JSONResponse(result, status_code=200)

    return router
