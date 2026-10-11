"""htmx routes for Listening: what you listen to on Spotify.

Read only: plays come in through the Spotify sync (or POST /listening/plays),
not through a form. After a sync the server sends `listening-changed`.
"""
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from .. import clock
from ..charts import day_of_month, last_days, last_weeks, percent, with_height
from ..web import render, toast
from .projections import (
    ListeningActivityProjection, RecentlyPlayedProjection, TopArtistsProjection, TopTracksProjection,
)
from .spotify import ListeningSync, RateLimited, SpotifyError

CHANGED = "listening-changed"
TABS = [("dashboard", "Dashboard"), ("history", "History"), ("progress", "Progress")]


def listening_summary(recent: RecentlyPlayedProjection, activity: ListeningActivityProjection,
                      top_artists: TopArtistsProjection) -> dict:
    """For the home screen and Settings. "This week" = the last 7 days, as with Reading."""
    today = clock.today()
    minutes = round(sum(b["value"] for b in last_days(activity.minutes_per_day(), today, 7)))
    plays = sum(b["value"] for b in last_days(activity.plays_per_day(), today, 7))
    last = recent.recent()[:1]
    return {
        "stats": [(minutes, "min this week"), (plays, "plays this week"),
                  (top_artists.count(today.year, today.month), "artists this month")],
        "current": [],  # no covers in this version
        "note": "Last played: " + " · ".join(filter(None, [last[0].track, ", ".join(last[0].artists)])) if last else None,
        "count": sum(activity.plays_per_day().values()),
        "unit": "plays",
    }


def create_listening_web_router(
    recent: RecentlyPlayedProjection,
    activity: ListeningActivityProjection,
    top_artists: TopArtistsProjection,
    top_tracks: TopTracksProjection,
    spotify_connected=lambda: False,
    spotify_profile=lambda: None,
) -> APIRouter:
    router = APIRouter(prefix="/ui/listening", include_in_schema=False)

    def top5_artists(today: date) -> list[dict]:
        top = top_artists.top(today.year, today.month, limit=5)
        most = top[0][1] if top else 0
        return [{"artist": a, "plays": n, "width": percent(n, most)} for a, n in top]

    @router.get("/dashboard")
    def dashboard(request: Request):
        today = clock.today()
        return render(request, "listening_dashboard.html", tabs=TABS,
                      summary=listening_summary(recent, activity, top_artists),
                      artists=top5_artists(today), recent=recent.recent()[:8],
                      week=with_height(last_days(activity.minutes_per_day(), today, 7)),
                      connected=spotify_connected(), profile=spotify_profile())

    @router.get("/history")
    def history(request: Request):
        return render(request, "listening_history.html", tabs=TABS, plays=recent.recent(),
                      connected=spotify_connected())

    @router.get("/progress")
    def progress(request: Request):
        today = clock.today()
        per_day = activity.minutes_per_day()
        return render(
            request, "listening_progress.html", tabs=TABS,
            days=with_height(last_days(per_day, today, 30, label=day_of_month)),
            weeks=with_height(last_weeks(per_day, today, 12)),
            tracks=top_tracks.top(today.year, today.month, limit=10),
            artists=top_artists.top(today.year, today.month, limit=10),
            month=today.strftime("%B %Y"),
            total_minutes=sum(per_day.values()),
        )

    return router


# ---- Connecting and syncing Spotify (Settings) ----

SPOTIFY_CHANGED = "spotify-changed"


def create_spotify_router(sync: ListeningSync) -> APIRouter:
    spotify = sync.spotify
    router = APIRouter(include_in_schema=False)

    def message(request: Request, title: str, text: str, status_code: int = 400):
        response = render(request, "spotify_message.html", title=title, text=text)
        response.status_code = status_code
        return response

    @router.get("/listening/spotify/connect")
    def connect(request: Request):
        """Sends you to Spotify to give Progen access to your listening history."""
        try:
            return RedirectResponse(spotify.authorize_url(), status_code=307)
        except SpotifyError as exc:
            return message(request, "Spotify isn't set up yet", str(exc))

    @router.get("/listening/spotify/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        """Spotify sends you back here after signing in."""
        if error:
            return message(request, "Spotify wasn't connected", f"Spotify said: {error}")
        try:
            spotify.finish_authorization(code, state)
        except SpotifyError as exc:
            return message(request, "Spotify wasn't connected", str(exc))
        sync.refresh_profile(force=True)  # name and photo for the dashboard
        sync.tick()  # a first sync right away; errors are logged, not shown
        return RedirectResponse("/#settings", status_code=303)

    @router.get("/ui/listening/spotify")
    def panel(request: Request):
        return render(request, "_spotify.html", configured=spotify.configured, connected=spotify.connected,
                      status=sync.status(), redirect_uri=spotify.redirect_uri,
                      interval_minutes=round(sync.interval_seconds / 60))

    @router.post("/ui/listening/spotify/sync")
    def sync_now(request: Request):
        try:
            result = sync.run()
        except RateLimited as exc:
            return toast(request, f"Spotify is busy right now; try again in {exc.retry_after} seconds.",
                         error=True, changed=SPOTIFY_CHANGED)
        except SpotifyError as exc:
            return toast(request, f"Sync failed: {exc}", error=True, changed=SPOTIFY_CHANGED)
        plays = "play" if result.stored == 1 else "plays"
        return toast(request, f"Synced with Spotify · {result.stored} new {plays}",
                     changed=f"{CHANGED}, {SPOTIFY_CHANGED}")

    @router.post("/ui/listening/spotify/disconnect")
    def disconnect(request: Request):
        sync.disconnect()
        return toast(request, "Spotify disconnected. Your plays stay in Progen.",
                     changed=f"{CHANGED}, {SPOTIFY_CHANGED}")

    @router.get("/listening/spotify/avatar")
    def avatar():
        """Your Spotify profile photo, stored locally."""
        if sync.profiles is None or not sync.profiles.avatar_path.exists():
            return Response(status_code=404)
        return FileResponse(sync.profiles.avatar_path, media_type="image/webp")

    return router
