import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .covers import CoverStore
from .eventstore import EventStore
from .reading.aggregate import DomainError
from .reading.api import create_reading_router
from .reading.commands import ReadingCommandHandler
from .reading.cover_search import CoverSearch
from .reading.events import ProgressLogged, SeriesRemoved, SeriesStarted, StatusChanged
from .reading.projections import LibraryProjection, ReadingActivityProjection
from .reading.web import create_reading_web_router
from .web import STATIC, create_settings_router

EVENT_TYPES = [SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved]


def create_app(
    db_path: str = "data/tracker.db",
    covers_dir: str | Path | None = None,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    """covers_dir staat standaard naast de database (data/covers). Tests geven
    een eigen map en een nep-http_client mee, zodat er niets naar buiten gaat."""
    store = EventStore(db_path, EVENT_TYPES)
    scratch = None
    if covers_dir is None and db_path == ":memory:":
        scratch = tempfile.TemporaryDirectory(prefix="progen-covers-")  # weg zodra de app weg is
        covers_dir = scratch.name
    elif covers_dir is None:
        covers_dir = Path(db_path).parent / "covers"
    http = http_client or httpx.Client(headers={"User-Agent": "Progen personal tracker"})
    covers = CoverStore(Path(covers_dir), http)
    cover_search = CoverSearch(http)

    library = LibraryProjection()
    activity = ReadingActivityProjection()
    projections = [library, activity]

    for event in store.load_all():
        for projection in projections:
            projection.apply(event)
    for projection in projections:
        store.subscribe(projection.apply)

    handler = ReadingCommandHandler(store, library)

    app = FastAPI(title="Personal Tracker")
    app.state.scratch_covers = scratch
    app.include_router(create_reading_router(handler, library, activity, covers))
    app.include_router(create_reading_web_router(handler, library, activity, covers, cover_search))
    app.include_router(create_settings_router(db_path, lambda: len(library.all())))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/covers", StaticFiles(directory=covers.directory), name="covers")

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
