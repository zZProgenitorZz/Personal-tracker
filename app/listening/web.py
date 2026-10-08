"""htmx-routes voor Listening: wat je op Spotify luistert.

Alleen lezen: plays komen binnen via de Spotify-sync (of POST /listening/plays),
niet via een formulier. Na een sync stuurt de server `listening-changed`.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from ..web import render, toast
from .projections import (
    ListeningActivityProjection, RecentlyPlayedProjection, TopArtistsProjection, TopTracksProjection,
)
from .spotify import ListeningSync, RateLimited, SpotifyError

CHANGED = "listening-changed"
TABS = [("dashboard", "Dashboard"), ("history", "History"), ("progress", "Progress")]


def last_days(per_day: dict, today: date, n: int) -> list[dict]:
    days = [today - timedelta(days=i) for i in reversed(range(n))]
    return [{"day": d, "label": d.strftime("%a")[0] if n <= 7 else str(d.day), "value": per_day.get(d, 0)} for d in days]


def last_weeks(per_day: dict, today: date, n: int) -> list[dict]:
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for i in reversed(range(n)):
        start = monday - timedelta(weeks=i)
        weeks.append({"start": start, "label": f"W{start.isocalendar()[1]}",
                      "value": sum(per_day.get(start + timedelta(days=d), 0) for d in range(7))})
    return weeks


def with_height(bars: list[dict]) -> list[dict]:
    top = max((b["value"] for b in bars), default=0)
    for bar in bars:
        bar["height"] = round(100 * bar["value"] / top) if top else 0
    return bars


def listening_summary(recent: RecentlyPlayedProjection, activity: ListeningActivityProjection,
                      top_artists: TopArtistsProjection) -> dict:
    """Voor het startscherm en Settings. "Deze week" = de laatste 7 dagen, zoals bij Reading."""
    today = date.today()
    minutes = round(sum(b["value"] for b in last_days(activity.minutes_per_day(), today, 7)))
    plays = sum(b["value"] for b in last_days(activity.plays_per_day(), today, 7))
    last = recent.recent()[:1]
    return {
        "stats": [(minutes, "min this week"), (plays, "plays this week"),
                  (len(top_artists.top(today.year, today.month, limit=10_000)), "artists this month")],
        "current": [],  # geen covers in deze versie
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
        return [{"artist": a, "plays": n, "width": round(100 * n / most) if most else 0} for a, n in top]

    @router.get("/dashboard")
    def dashboard(request: Request):
        today = date.today()
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
        today = date.today()
        per_day = activity.minutes_per_day()
        return render(
            request, "listening_progress.html", tabs=TABS,
            days=with_height(last_days(per_day, today, 30)),
            weeks=with_height(last_weeks(per_day, today, 12)),
            tracks=top_tracks.top(today.year, today.month, limit=10),
            artists=top_artists.top(today.year, today.month, limit=10),
            month=today.strftime("%B %Y"),
            total_minutes=sum(per_day.values()),
        )

    return router


# ---- Spotify koppelen en synchroniseren (Settings) ----

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
        """Stuurt je naar Spotify om Progen toegang te geven tot je luistergeschiedenis."""
        try:
            return RedirectResponse(spotify.authorize_url(), status_code=307)
        except SpotifyError as exc:
            return message(request, "Spotify isn't set up yet", str(exc))

    @router.get("/listening/spotify/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        """Hier stuurt Spotify je terug na het inloggen."""
        if error:
            return message(request, "Spotify wasn't connected", f"Spotify said: {error}")
        try:
            spotify.finish_authorization(code, state)
        except SpotifyError as exc:
            return message(request, "Spotify wasn't connected", str(exc))
        sync.refresh_profile(force=True)  # naam en foto voor het dashboard
        sync.tick()  # meteen een eerste sync; fouten worden gelogd, niet getoond
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
        """Je Spotify-profielfoto, lokaal bewaard."""
        if sync.profiles is None or not sync.profiles.avatar_path.exists():
            return Response(status_code=404)
        return FileResponse(sync.profiles.avatar_path, media_type="image/webp")

    return router
