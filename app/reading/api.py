from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..covers import CoverError, CoverStore, with_cover
from .commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, SetGenres, StartSeries
from .events import Kind, Status
from .projections import LibraryProjection, ReadingActivityProjection


# ---- What the API expects to receive ----

class StartSeriesBody(BaseModel):
    title: str = Field(min_length=1)
    kind: Kind
    source: str
    start_chapter: float = 0
    cover_url: str | None = None
    status: Status = Status.READING
    genres: list[str] = []


class GenresBody(BaseModel):
    genres: list[str]


class ProgressBody(BaseModel):
    chapter: float


class StatusBody(BaseModel):
    status: Status


# ---- The endpoints ----

def create_reading_router(
    handler: ReadingCommandHandler,
    library: LibraryProjection,
    activity: ReadingActivityProjection,
    covers: CoverStore,
) -> APIRouter:
    router = APIRouter(prefix="/reading", tags=["reading"])

    def entry_or_404(series_id: str):
        entry = library.get(series_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Series not found")
        return entry

    # Reading: questions for the read models

    @router.get("/current")
    def currently_reading():
        return library.currently_reading()

    @router.get("/library")
    def get_library(status: Status | None = None):
        if status is not None:
            return library.by_status(status)
        return library.all()

    @router.get("/activity")
    def get_activity():
        return {
            "per_day": {str(day): n for day, n in activity.per_day().items()},
            "per_week": activity.per_week(),
        }

    # Writing: commands to the handler

    @router.post("/series", status_code=201)
    def start_series(body: StartSeriesBody):
        # A failed cover doesn't block saving; the reason is in cover_error.
        cover, cover_error = None, None
        if body.cover_url:
            try:
                cover = covers.save_from_url(body.cover_url)
            except CoverError as exc:
                cover_error = str(exc)
        events = with_cover(covers, cover, lambda: handler.handle(
            StartSeries(body.title, body.kind, body.source, body.start_chapter, cover, body.status, tuple(body.genres))
        ))
        entry = entry_or_404(events[0].series_id)
        return {**asdict(entry), "cover_error": cover_error} if cover_error else entry

    @router.post("/series/{series_id}/progress")
    def log_progress(series_id: str, body: ProgressBody):
        entry_or_404(series_id)
        handler.handle(LogProgress(series_id, body.chapter))
        return entry_or_404(series_id)

    @router.post("/series/{series_id}/status")
    def change_status(series_id: str, body: StatusBody):
        entry_or_404(series_id)
        handler.handle(ChangeStatus(series_id, body.status))
        return entry_or_404(series_id)

    @router.post("/series/{series_id}/genres")
    def set_genres(series_id: str, body: GenresBody):
        entry_or_404(series_id)
        handler.handle(SetGenres(series_id, tuple(body.genres)))
        return entry_or_404(series_id)

    @router.delete("/series/{series_id}", status_code=204)
    def remove_series(series_id: str):
        entry_or_404(series_id)
        handler.handle(RemoveSeries(series_id))

    return router