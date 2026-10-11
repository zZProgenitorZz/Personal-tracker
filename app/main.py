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

from . import clock
from .assistant.commands import AssistantCommandHandler
from .assistant.tools import TrackerData
from .assistant.session import Sunny
from .assistant.web import create_assistant_router
from .autostart import Autostart, launcher_command
from .backup import Backups, default_backup_dir
from .covers import CoverStore
from .domain import DomainError
from .event_types import ALL_EVENT_TYPES
from .eventstore import EventStore
from .listening.api import create_listening_router
from .listening.commands import ListeningCommandHandler
from .listening.import_web import ImportJob, create_import_router
from .listening.projections import (
    ListeningActivityProjection, RecentlyPlayedProjection, TopArtistsProjection, TopTracksProjection,
)
from .listening.spotify import ListeningSync, ProfileStore, Spotify, TokenFile, sync_interval_seconds
from .listening.web import create_listening_web_router, create_spotify_router, listening_summary
from .planner.api import create_planner_router
from .planner.commands import PlannerCommandHandler
from .planner.projections import AgendaProjection, PlannerActivityProjection
from .planner.web import create_planner_web_router, planner_summary
from .reading.api import create_reading_router
from .reading.commands import ReadingCommandHandler
from .cover_search import CoverSearch
from .reading.cover_sources import SOURCES as READING_SOURCES
from .reading.projections import LibraryProjection, ReadingActivityProjection, ReadingHistoryProjection
from .reading.web import create_reading_web_router, reading_summary
from .security import ALLOWED_HOSTS, protect
from .settings_web import create_settings_router
from .shutdown import create_shutdown_router, stop_this_server
from .watching.api import create_watching_router
from .watching.commands import WatchingCommandHandler
from .watching.cover_sources import SOURCES as WATCHING_SOURCES
from .watching.projections import WatchActivityProjection, WatchHistoryProjection, WatchlistProjection
from .watching.web import create_watching_web_router, watching_summary
from .web import STATIC, Tracker, create_home_router, render
from .wrapped.projections import WrappedProjection
from .wrapped.web import create_wrapped_web_router, wrapped_teaser

# Settings such as GOOGLE_BOOKS_API_KEY. Not in git (.gitignore).
ENV_FILE = Path(__file__).parent.parent / ".env"

def create_app(
    db_path: str = "data/tracker.db",
    covers_dir: str | Path | None = None,
    http_client: httpx.Client | None = None,
    env_file: Path = ENV_FILE,
    backup_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> FastAPI:
    """covers_dir sits next to the database by default (data/covers). Tests pass
    their own folder and a fake http_client, so nothing goes out.
    Settings from env_file only apply when they aren't already in the environment."""
    load_dotenv(env_file, override=False)
    store = EventStore(db_path, ALL_EVENT_TYPES)
    if db_path == ":memory:":
        # Tests: covers and backups in a temporary folder, never in data/ or your OneDrive.
        scratch = Path(tempfile.mkdtemp(prefix="progen-"))  # cleaned up as soon as the app is gone
        covers_dir = covers_dir or scratch / "covers"
        backup_dir = backup_dir or scratch / "backups"
        data_dir = data_dir or scratch
    else:
        scratch = None
        covers_dir = covers_dir or Path(db_path).parent / "covers"
        backup_dir = backup_dir or default_backup_dir()
        data_dir = data_dir or Path(db_path).parent  # next to tracker.db, so not in git
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
    wrapped = WrappedProjection()  # year in review across all trackers, from the same events
    agenda = AgendaProjection()
    planner_activity = PlannerActivityProjection()
    # What happened when, removed items included: for Sunny's history tools.
    reading_history, watch_history = ReadingHistoryProjection(), WatchHistoryProjection()
    projections = [library, activity, watchlist, watch_activity, recent_plays, listen_activity, top_artists, top_tracks,
                   wrapped, agenda, planner_activity, reading_history, watch_history]

    def rebuild() -> None:
        """Rebuilds the read models from all events (at startup and after a restore)."""
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
    # Spotify tokens and the last sync in separate files: not in the event store and not in a backup.
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
        """Background task: a Spotify sync every SYNC_INTERVAL_MINUTES (only when there is a token).
        tick() catches all errors itself, so this loop only stops when the app stops."""
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
    app.state.request_shutdown = stop_this_server  # tests replace this with something harmless
    # Tests (":memory:") never touch the real Windows registry.
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
    # Importing your whole Spotify history (Settings); the projections follow along live.
    app.include_router(create_import_router(ImportJob(store, backups)))
    app.include_router(create_planner_router(planner, agenda))
    # Sunny, the assistant: reads the projections, only writes after Confirm through the same handlers.
    # Never a real client in the tests (":memory:"); they give it a fake client themselves.
    sunny = Sunny(TrackerData(library=library, watchlist=watchlist, listening=listen_activity,
                              top_artists=top_artists, top_tracks=top_tracks, agenda=agenda,
                              reading_activity=activity, reading_history=reading_history, watch_history=watch_history, reading=handler,
                              watching=watching, planner=planner, now=lambda: clock.local_now()),
                  AssistantCommandHandler(store), use_real_client=scratch is None)
    app.state.sunny = sunny
    app.include_router(create_assistant_router(sunny))
    app.include_router(create_planner_web_router(planner, agenda))
    app.include_router(create_wrapped_web_router(wrapped))
    # Wrapped isn't a tracker (not in Settings or backups), only a card on the home screen.
    app.include_router(create_home_router(trackers, wrapped=lambda: wrapped_teaser(wrapped)))
    app.include_router(create_settings_router(db_path, trackers, backups))
    app.include_router(create_shutdown_router())
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/covers", StaticFiles(directory=covers.directory), name="covers")

    @app.middleware("http")
    async def always_fresh(request: Request, call_next):
        """Page, scripts and fragments: the browser always checks whether they are still
        current, so you never see an old version after an update. Covers
        (fixed, unique names) may simply come from the cache."""
        response = await call_next(request)
        if not request.url.path.startswith("/covers/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # Only 127.0.0.1/localhost, and no changes from other websites (see app/security.py).
    # Tests talk to the app as "testserver".
    protect(app, ALLOWED_HOSTS + ["testserver"] if scratch else ALLOWED_HOSTS)

    @app.exception_handler(DomainError)
    def handle_domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return render(request, "index.html")

    return app
