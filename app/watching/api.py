from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..covers import CoverError, CoverStore
from ..domain import DomainError
from .commands import AddShow, ChangeShowStatus, RemoveShow, SetShowGenres, WatchingCommandHandler
from .events import WatchKind, WatchStatus
from .projections import WatchActivityProjection, WatchlistProjection


# ---- Wat de API binnen verwacht ----

class AddShowBody(BaseModel):
    title: str = Field(min_length=1)
    kind: WatchKind
    status: WatchStatus = WatchStatus.WATCHING
    genres: list[str] = []
    cover_url: str | None = None


class StatusBody(BaseModel):
    status: WatchStatus


class GenresBody(BaseModel):
    genres: list[str]


# ---- De endpoints ----

def create_watching_router(
    handler: WatchingCommandHandler,
    watchlist: WatchlistProjection,
    activity: WatchActivityProjection,
    covers: CoverStore,
) -> APIRouter:
    router = APIRouter(prefix="/watching", tags=["watching"])

    def entry_or_404(show_id: str):
        entry = watchlist.get(show_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Titel niet gevonden")
        return entry

    # Lezen: vragen aan de read models

    @router.get("/list")
    def get_list(status: WatchStatus | None = None):
        return watchlist.by_status(status) if status is not None else watchlist.all()

    @router.get("/current")
    def currently_watching():
        return watchlist.currently_watching()

    @router.get("/activity")
    def get_activity():
        return {"finished_per_day": {str(day): n for day, n in activity.per_day().items()}}

    # Schrijven: commands naar de handler

    @router.post("/shows", status_code=201)
    def add_show(body: AddShowBody):
        # Een mislukte cover blokkeert het toevoegen niet; de reden staat in cover_error.
        cover, cover_error = None, None
        if body.cover_url:
            try:
                cover = covers.save_from_url(body.cover_url)
            except CoverError as exc:
                cover_error = str(exc)
        try:
            events = handler.handle(AddShow(body.title, body.kind, body.status, cover, tuple(body.genres)))
        except DomainError:
            if cover:
                covers.delete(cover)
            raise
        entry = entry_or_404(events[0].show_id)
        return {**asdict(entry), "cover_error": cover_error} if cover_error else entry

    @router.post("/shows/{show_id}/status")
    def change_status(show_id: str, body: StatusBody):
        entry_or_404(show_id)
        handler.handle(ChangeShowStatus(show_id, body.status))
        return entry_or_404(show_id)

    @router.post("/shows/{show_id}/genres")
    def set_genres(show_id: str, body: GenresBody):
        entry_or_404(show_id)
        handler.handle(SetShowGenres(show_id, tuple(body.genres)))
        return entry_or_404(show_id)

    @router.delete("/shows/{show_id}", status_code=204)
    def remove_show(show_id: str):
        entry_or_404(show_id)
        handler.handle(RemoveShow(show_id))

    return router
