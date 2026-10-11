from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import clock
from .commands import ListeningCommandHandler, RecordPlay, stream_id
from .projections import (
    ListeningActivityProjection, RecentlyPlayedProjection, TopArtistsProjection, TopTracksProjection,
)


# ---- What the API expects to receive ----

class PlayBody(BaseModel):
    played_at: datetime  # without a time zone, UTC applies
    track_id: str
    track: str
    artists: list[str] = []
    album: str = ""
    album_id: str = ""
    duration_ms: int
    ms_played: int | None = None
    source: str = "api"


MONTH = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM; this month by default")


def _year_month(month: str | None) -> tuple[int, int]:
    if month is None:
        today = clock.today()
        return today.year, today.month
    year, number = month.split("-")
    return int(year), int(number)


# ---- The endpoints ----

def create_listening_router(
    handler: ListeningCommandHandler,
    recent: RecentlyPlayedProjection,
    activity: ListeningActivityProjection,
    top_artists: TopArtistsProjection,
    top_tracks: TopTracksProjection,
) -> APIRouter:
    router = APIRouter(prefix="/listening", tags=["listening"])

    # Reading: questions for the read models

    @router.get("/recent")
    def get_recent():
        return recent.recent()

    @router.get("/activity")
    def get_activity():
        return {
            "minutes_per_day": {str(day): round(m, 2) for day, m in activity.minutes_per_day().items()},
            "minutes_per_week": {week: round(m, 2) for week, m in activity.minutes_per_week().items()},
        }

    def period(start: date | None, end: date | None) -> tuple[date, date] | None:
        """from and to together (inclusive), otherwise None: then month applies."""
        if start is None and end is None:
            return None
        if start is None or end is None or end < start:
            raise HTTPException(status_code=422, detail="Give both 'from' and 'to', with 'from' not after 'to'")
        return start, end

    @router.get("/top-artists")
    def get_top_artists(month: str | None = MONTH, limit: int = 10,
                        start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to")):
        """Per month (month=YYYY-MM, this one by default), or a free period with from and to (YYYY-MM-DD)."""
        found = period(start, end)
        ranked = top_artists.between(*found, limit=limit) if found else top_artists.top(*_year_month(month), limit=limit)
        return [{"artist": a, "plays": n} for a, n in ranked]

    @router.get("/top-tracks")
    def get_top_tracks(month: str | None = MONTH, limit: int = 10,
                       start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to")):
        """Per month (month=YYYY-MM, this one by default), or a free period with from and to (YYYY-MM-DD)."""
        found = period(start, end)
        return top_tracks.between(*found, limit=limit) if found else top_tracks.top(*_year_month(month), limit=limit)

    # Writing: commands to the handler

    @router.post("/plays", status_code=201)
    def record_play(body: PlayBody):
        """201 if the play was stored, 200 if this moment was already known (skipped)."""
        command = RecordPlay(body.played_at, body.track_id, body.track, body.artists, body.album,
                             body.album_id, body.duration_ms, body.ms_played, body.source)
        stored = bool(handler.handle(command))
        result = {"stored": stored, "stream_id": stream_id(body.played_at)}
        return result if stored else JSONResponse(result, status_code=200)

    return router
