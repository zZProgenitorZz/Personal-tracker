import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .backup import Backups, default_backup_dir
from .covers import CoverStore
from .domain import DomainError
from .eventstore import EventStore
from .reading.api import create_reading_router
from .reading.commands import ReadingCommandHandler
from .cover_search import CoverSearch
from .reading.cover_sources import SOURCES as READING_SOURCES
from .reading.events import GenresChanged, ProgressLogged, SeriesRemoved, SeriesStarted, StatusChanged
from .reading.projections import LibraryProjection, ReadingActivityProjection
from .reading.web import create_reading_web_router, reading_summary
from .watching.api import create_watching_router
from .watching.commands import WatchingCommandHandler
from .watching.cover_sources import SOURCES as WATCHING_SOURCES
from .watching.events import ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged
from .watching.projections import WatchActivityProjection, WatchlistProjection
from .watching.web import create_watching_web_router, watching_summary
from .web import STATIC, Tracker, create_home_router, create_settings_router, render

# Instellingen zoals GOOGLE_BOOKS_API_KEY. Staat niet in git (.gitignore).
ENV_FILE = Path(__file__).parent.parent / ".env"

# Alle trackers delen één event store (één database, één back-up).
EVENT_TYPES = [
    SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved, GenresChanged,   # reading
    ShowAdded, ShowStatusChanged, ShowGenresChanged, ShowRemoved,                 # watching
]


def create_app(
    db_path: str = "data/tracker.db",
    covers_dir: str | Path | None = None,
    http_client: httpx.Client | None = None,
    env_file: Path = ENV_FILE,
    backup_dir: str | Path | None = None,
) -> FastAPI:
    """covers_dir staat standaard naast de database (data/covers). Tests geven
    een eigen map en een nep-http_client mee, zodat er niets naar buiten gaat.
    Instellingen uit env_file gelden alleen als ze niet al in de omgeving staan."""
    load_dotenv(env_file, override=False)
    store = EventStore(db_path, EVENT_TYPES)
    if db_path == ":memory:":
        # Tests: covers en back-ups in een tijdelijke map, nooit in data/ of je OneDrive.
        scratch = tempfile.TemporaryDirectory(prefix="progen-")  # weg zodra de app weg is
        covers_dir = covers_dir or Path(scratch.name) / "covers"
        backup_dir = backup_dir or Path(scratch.name) / "backups"
    else:
        scratch = None
        covers_dir = covers_dir or Path(db_path).parent / "covers"
        backup_dir = backup_dir or default_backup_dir()
    http = http_client or httpx.Client(headers={"User-Agent": "Progen personal tracker"})
    covers = CoverStore(Path(covers_dir), http)
    reading_search = CoverSearch(http, sources=READING_SOURCES)
    watching_search = CoverSearch(http, sources=WATCHING_SOURCES)

    library = LibraryProjection()
    activity = ReadingActivityProjection()
    watchlist = WatchlistProjection()
    watch_activity = WatchActivityProjection()
    projections = [library, activity, watchlist, watch_activity]

    def rebuild() -> None:
        """Read models opnieuw opbouwen uit alle events (bij start en na terugzetten)."""
        for projection in projections:
            projection.reset()
        for event in store.load_all():
            for projection in projections:
                projection.apply(event)

    rebuild()
    for projection in projections:
        store.subscribe(projection.apply)
    backups = Backups(store, covers.directory, Path(backup_dir), on_restored=rebuild)

    handler = ReadingCommandHandler(store, library)
    watching = WatchingCommandHandler(store, watchlist)
    trackers = [
        Tracker("reading", "Reading", "book", "reading-changed", lambda: reading_summary(library, activity)),
        Tracker("watching", "Watching", "eye", "watching-changed", lambda: watching_summary(watchlist, watch_activity)),
    ]

    app = FastAPI(title="Personal Tracker")
    app.state.scratch_covers = scratch
    app.include_router(create_reading_router(handler, library, activity, covers))
    app.include_router(create_reading_web_router(handler, library, activity, covers, reading_search))
    app.include_router(create_watching_router(watching, watchlist, watch_activity, covers))
    app.include_router(create_watching_web_router(watching, watchlist, watch_activity, covers, watching_search))
    app.include_router(create_home_router(trackers))
    app.include_router(create_settings_router(db_path, trackers, backups))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/covers", StaticFiles(directory=covers.directory), name="covers")

    @app.middleware("http")
    async def always_fresh(request: Request, call_next):
        """Pagina, scripts en fragmenten: de browser vraagt altijd even na of ze nog
        actueel zijn, zodat je na een update nooit een oude versie ziet. Covers
        (vaste, unieke namen) mogen gewoon uit de cache komen."""
        response = await call_next(request)
        if not request.url.path.startswith("/covers/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return render(request, "index.html")

    return app
