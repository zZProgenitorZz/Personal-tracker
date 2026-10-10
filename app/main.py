import asyncio
import shutil
import tempfile
import weakref
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .autostart import Autostart, launcher_command
from .backup import Backups, default_backup_dir
from .covers import CoverStore
from .domain import DomainError
from .eventstore import EventStore
from .listening.api import create_listening_router
from .listening.commands import ListeningCommandHandler
from .listening.events import TrackPlayed
from .listening.import_web import ImportJob, create_import_router
from .listening.projections import (
    ListeningActivityProjection, RecentlyPlayedProjection, TopArtistsProjection, TopTracksProjection,
)
from .listening.spotify import ListeningSync, ProfileStore, Spotify, TokenFile, sync_interval_seconds
from .listening.web import create_listening_web_router, create_spotify_router, listening_summary
from .planner.api import create_planner_router
from .planner.commands import PlannerCommandHandler
from .planner.events import EVENT_TYPES as PLANNER_EVENT_TYPES
from .planner.projections import AgendaProjection, PlannerActivityProjection
from .planner.web import create_planner_web_router, planner_summary
from .reading.api import create_reading_router
from .reading.commands import ReadingCommandHandler
from .cover_search import CoverSearch
from .reading.cover_sources import SOURCES as READING_SOURCES
from .reading.events import GenresChanged, ProgressLogged, SeriesRemoved, SeriesStarted, StatusChanged
from .reading.projections import LibraryProjection, ReadingActivityProjection
from .reading.web import create_reading_web_router, reading_summary
from .security import ALLOWED_HOSTS, protect
from .shutdown import create_shutdown_router, stop_this_server
from .watching.api import create_watching_router
from .watching.commands import WatchingCommandHandler
from .watching.cover_sources import SOURCES as WATCHING_SOURCES
from .watching.events import ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged
from .watching.projections import WatchActivityProjection, WatchlistProjection
from .watching.web import create_watching_web_router, watching_summary
from .web import STATIC, Tracker, create_home_router, create_settings_router, render
from .wrapped.projections import WrappedProjection
from .wrapped.web import create_wrapped_web_router, wrapped_teaser

# Instellingen zoals GOOGLE_BOOKS_API_KEY. Staat niet in git (.gitignore).
ENV_FILE = Path(__file__).parent.parent / ".env"

# Alle trackers delen één event store (één database, één back-up).
EVENT_TYPES = [
    SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved, GenresChanged,   # reading
    ShowAdded, ShowStatusChanged, ShowGenresChanged, ShowRemoved,                 # watching
    TrackPlayed,                                                                  # listening
    *PLANNER_EVENT_TYPES,                                                         # planner
]


def create_app(
    db_path: str = "data/tracker.db",
    covers_dir: str | Path | None = None,
    http_client: httpx.Client | None = None,
    env_file: Path = ENV_FILE,
    backup_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> FastAPI:
    """covers_dir staat standaard naast de database (data/covers). Tests geven
    een eigen map en een nep-http_client mee, zodat er niets naar buiten gaat.
    Instellingen uit env_file gelden alleen als ze niet al in de omgeving staan."""
    load_dotenv(env_file, override=False)
    store = EventStore(db_path, EVENT_TYPES)
    if db_path == ":memory:":
        # Tests: covers en back-ups in een tijdelijke map, nooit in data/ of je OneDrive.
        scratch = Path(tempfile.mkdtemp(prefix="progen-"))  # wordt opgeruimd zodra de app weg is
        covers_dir = covers_dir or scratch / "covers"
        backup_dir = backup_dir or scratch / "backups"
        data_dir = data_dir or scratch
    else:
        scratch = None
        covers_dir = covers_dir or Path(db_path).parent / "covers"
        backup_dir = backup_dir or default_backup_dir()
        data_dir = data_dir or Path(db_path).parent  # naast tracker.db, dus niet in git
    http = http_client or httpx.Client(headers={"User-Agent": "Progen personal tracker"})
    covers = CoverStore(Path(covers_dir), http)
    reading_search = CoverSearch(http, sources=READING_SOURCES)
    watching_search = CoverSearch(http, sources=WATCHING_SOURCES)

    library = LibraryProjection()
    activity = ReadingActivityProjection()
    watchlist = WatchlistProjection()
    watch_activity = WatchActivityProjection()
    recent_plays = RecentlyPlayedProjection()
    listen_activity = ListeningActivityProjection()
    top_artists = TopArtistsProjection()
    top_tracks = TopTracksProjection()
    wrapped = WrappedProjection()  # jaaroverzicht over alle trackers heen, uit dezelfde events
    agenda = AgendaProjection()
    planner_activity = PlannerActivityProjection()
    projections = [library, activity, watchlist, watch_activity, recent_plays, listen_activity, top_artists, top_tracks,
                   wrapped, agenda, planner_activity]

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
    listening = ListeningCommandHandler(store)
    planner = PlannerCommandHandler(store)
    # Spotify-tokens en de laatste sync in losse bestanden: niet in de event store en niet in een back-up.
    spotify = Spotify.from_environment(http, TokenFile(Path(data_dir) / "spotify_token.json"))
    listening_sync = ListeningSync(spotify, listening, recent_plays, Path(data_dir) / "spotify_sync.json",
                                   interval_seconds=sync_interval_seconds(),
                                   profiles=ProfileStore(Path(data_dir)), download=covers.download)
    trackers = [
        Tracker("reading", "Reading", "book", "reading-changed", lambda: reading_summary(library, activity)),
        Tracker("watching", "Watching", "eye", "watching-changed", lambda: watching_summary(watchlist, watch_activity)),
        Tracker("listening", "Listening", "music", "listening-changed",
                lambda: listening_summary(recent_plays, listen_activity, top_artists)),
        Tracker("planner", "Planner", "calendar", "planner-changed", lambda: planner_summary(agenda)),
    ]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Achtergrondtaak: elke SYNC_INTERVAL_MINUTES een Spotify-sync (alleen als er een token is).
        tick() vangt alle fouten zelf af, dus deze lus stopt alleen als de app stopt."""
        async def sync_forever():
            while True:
                wait = await asyncio.to_thread(listening_sync.tick)
                await asyncio.sleep(wait)

        task = asyncio.create_task(sync_forever())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="Personal Tracker", lifespan=lifespan)
    if scratch:
        weakref.finalize(app, shutil.rmtree, scratch, ignore_errors=True)
    app.state.request_shutdown = stop_this_server  # tests vervangen dit door iets onschuldigs
    # Tests (":memory:") raken het echte Windows-register nooit aan.
    app.state.autostart = Autostart(None, launcher_command()) if scratch else Autostart.for_this_computer()
    app.include_router(create_reading_router(handler, library, activity, covers))
    app.include_router(create_reading_web_router(handler, library, activity, covers, reading_search))
    app.include_router(create_watching_router(watching, watchlist, watch_activity, covers))
    app.include_router(create_watching_web_router(watching, watchlist, watch_activity, covers, watching_search))
    app.include_router(create_listening_router(listening, recent_plays, listen_activity, top_artists, top_tracks))
    app.include_router(create_listening_web_router(recent_plays, listen_activity, top_artists, top_tracks,
                                                   spotify_connected=lambda: spotify.connected,
                                                   spotify_profile=lambda: spotify.connected and listening_sync.profiles.load()))
    app.include_router(create_spotify_router(listening_sync))
    # Je hele Spotify-geschiedenis importeren (Settings); de projecties lopen live mee.
    app.include_router(create_import_router(ImportJob(store, backups)))
    app.include_router(create_planner_router(planner, agenda))
    app.include_router(create_planner_web_router(planner, agenda))
    app.include_router(create_wrapped_web_router(wrapped))
    # Wrapped is geen tracker (niet in Settings of back-ups), alleen een kaart op het startscherm.
    app.include_router(create_home_router(trackers, wrapped=lambda: wrapped_teaser(wrapped)))
    app.include_router(create_settings_router(db_path, trackers, backups))
    app.include_router(create_shutdown_router())
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

    # Alleen 127.0.0.1/localhost, en geen wijzigingen vanaf andere websites (zie app/security.py).
    # Tests spreken de app aan als "testserver".
    protect(app, ALLOWED_HOSTS + ["testserver"] if scratch else ALLOWED_HOSTS)

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return render(request, "index.html")

    return app
