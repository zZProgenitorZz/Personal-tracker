from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse

from .eventstore import EventStore
from .reading.aggregate import DomainError
from .reading.api import create_reading_router
from .reading.commands import ReadingCommandHandler
from .reading.events import ProgressLogged, SeriesStarted, StatusChanged
from .reading.projections import LibraryProjection, ReadingActivityProjection
from pathlib import Path

STATIC = Path(__file__).parent / "static"
EVENT_TYPES = [SeriesStarted, ProgressLogged, StatusChanged]


def create_app(db_path: str = "data/tracker.db") -> FastAPI:
    store = EventStore(db_path, EVENT_TYPES)

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
    app.include_router(create_reading_router(handler, library, activity))

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")
    return app