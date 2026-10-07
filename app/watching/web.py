"""htmx-routes voor Watching: films, series en anime.

GET-routes geven HTML-fragmenten uit de read models. POST-routes sturen
commands en antwoorden met een toast plus het event `watching-changed`,
waarop de zichtbare onderdelen zichzelf opnieuw ophalen.
"""
from datetime import date

from fastapi import APIRouter, File, Form, Request, UploadFile

from ..cover_search import CoverSearch
from ..covers import CoverError, CoverStore
from ..domain import DomainError
from ..web import render, save_cover, templates, toast
from .commands import AddShow, ChangeShowStatus, RemoveShow, SetShowGenres, WatchingCommandHandler
from .events import WatchKind, WatchStatus
from .genres import WATCH_GENRES
from .projections import WatchActivityProjection, WatchEntry, WatchlistProjection

CHANGED = "watching-changed"

WATCH_STATUS_LABELS = {
    WatchStatus.WATCHING: "Watching",
    WatchStatus.COMPLETED: "Completed",
    WatchStatus.ON_HOLD: "On hold",
    WatchStatus.DROPPED: "Dropped",
}
WATCH_KIND_LABELS = {WatchKind.SERIES: "Series", WatchKind.ANIME: "Anime", WatchKind.MOVIE: "Movie"}

templates.env.globals.update(
    WATCH_STATUS_LABELS=WATCH_STATUS_LABELS, WATCH_KIND_LABELS=WATCH_KIND_LABELS, WATCH_GENRES=WATCH_GENRES.names,
)


def finished_per_month(per_day: dict[date, int], today: date, months: int) -> list[dict]:
    """Afgerond per kalendermaand, de laatste `months` maanden (de huidige als laatste)."""
    result = []
    year, month = today.year, today.month
    for _ in range(months):
        result.append({"label": date(year, month, 1).strftime("%b"), "year": year, "month": month,
                       "value": sum(n for d, n in per_day.items() if (d.year, d.month) == (year, month))})
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    result.reverse()
    top = max((m["value"] for m in result), default=0)
    for m in result:
        m["height"] = round(100 * m["value"] / top) if top else 0
    return result


def watching_summary(watchlist: WatchlistProjection, activity: WatchActivityProjection) -> dict:
    """Voor het startscherm en Settings."""
    today = date.today()
    current = watchlist.currently_watching()
    this_month = sum(n for d, n in activity.per_day().items() if (d.year, d.month) == (today.year, today.month))
    return {
        "stats": [(len(current), "watching now"), (this_month, "finished this month"),
                  (len(watchlist.by_status(WatchStatus.COMPLETED)), "completed")],
        "current": current[:5],
        "count": len(watchlist.all()),
        "unit": "titles",
    }


def create_watching_web_router(
    handler: WatchingCommandHandler,
    watchlist: WatchlistProjection,
    activity: WatchActivityProjection,
    covers: CoverStore,
    cover_search: CoverSearch,
) -> APIRouter:
    router = APIRouter(prefix="/ui/watching", include_in_schema=False)

    def counts() -> dict:
        entries = watchlist.all()
        return {
            "status": [{"status": s, "label": label, "count": sum(e.status is s for e in entries)}
                       for s, label in WATCH_STATUS_LABELS.items()],
            "kind": [{"kind": k, "label": label, "count": sum(e.kind is k for e in entries)}
                     for k, label in WATCH_KIND_LABELS.items()],
            "total": len(entries),
        }

    def filtered(status: str, kind: str, genre: str, q: str) -> list[WatchEntry]:
        entries = watchlist.all()
        if status in WatchStatus._value2member_map_:
            entries = [e for e in entries if e.status is WatchStatus(status)]
        if kind in WatchKind._value2member_map_:
            entries = [e for e in entries if e.kind is WatchKind(kind)]
        if genre:
            entries = [e for e in entries if genre in e.genres]
        if q.strip():
            entries = [e for e in entries if q.strip().lower() in e.title.lower()]
        return entries

    # Lezen

    @router.get("/dashboard")
    def dashboard(request: Request):
        summary = watching_summary(watchlist, activity)
        return render(request, "watching_dashboard.html", summary=summary,
                      current=watchlist.currently_watching()[:6], counts=counts())

    @router.get("/library")
    def library_page(request: Request):
        return render(request, "watching_library.html", entries=watchlist.all(), status="", kind="", genre="", q="")

    @router.get("/library/grid")
    def library_grid(request: Request, status: str = "", kind: str = "", genre: str = "", q: str = ""):
        return render(request, "_watching_grid.html", entries=filtered(status, kind, genre, q),
                      status=status, kind=kind, genre=genre, q=q)

    @router.get("/progress")
    def progress(request: Request):
        per_day = activity.per_day()
        entries = watchlist.all()
        genre_counts = {g: sum(g in e.genres for e in entries) for g in WATCH_GENRES.names}
        top = max(genre_counts.values(), default=0)
        return render(
            request, "watching_progress.html",
            months=finished_per_month(per_day, date.today(), 12),
            finished_total=sum(per_day.values()),
            summary=watching_summary(watchlist, activity),
            counts=counts(),
            genres=[{"genre": g, "count": n, "width": round(100 * n / top) if top else 0}
                    for g, n in sorted(genre_counts.items(), key=lambda i: -i[1]) if n],
        )

    @router.get("/add-form")
    def add_form(request: Request):
        return render(request, "_watching_add_form.html")

    @router.get("/covers/search")
    def find_cover(request: Request, title: str = "", index: int = 0):
        """Hulp bij het formulier: één zoekresultaat tegelijk. Slaat niets op."""
        if not title.strip():
            return render(request, "_cover_pick.html", error="Type a title first, then search for its cover.")
        try:
            results = cover_search.search(title)
        except CoverError as exc:
            return render(request, "_cover_pick.html", error=str(exc))
        if not results:
            return render(request, "_cover_pick.html",
                          error=f"No covers found for “{title.strip()}”. Try the official title, or add one yourself.")
        i = index % len(results)
        return render(request, "_cover_pick.html", pick=results[i], position=i + 1, total=len(results),
                      next_index=(i + 1) % len(results))

    @router.get("/shows/{show_id}/genres")
    def edit_genres(request: Request, show_id: str):
        entry = watchlist.get(show_id)
        if entry is None:
            return toast(request, "Onbekende titel", error=True)
        return render(request, "_genre_editor.html", title=entry.title, options=WATCH_GENRES.names,
                      selected=entry.genres, action=f"/ui/watching/shows/{show_id}/genres")

    # Schrijven

    @router.post("/shows")
    def add_show(
        request: Request,
        title: str = Form(),
        kind: WatchKind = Form(),
        status: WatchStatus = Form(WatchStatus.WATCHING),
        genres: list[str] = Form([]),
        cover_file: UploadFile | None = File(None),
        cover_link: str = Form(""),
        cover_url: str = Form(""),
    ):
        cover, cover_problem = None, None
        try:
            cover = save_cover(covers, cover_file, cover_link, cover_url)
        except CoverError as exc:
            cover_problem = str(exc)
        try:
            handler.handle(AddShow(title, kind, status, cover, tuple(genres)))
        except DomainError as exc:
            if cover:
                covers.delete(cover)
            return toast(request, str(exc), error=True)
        label = f"{title.strip()} ({WATCH_KIND_LABELS[kind]}, {WATCH_STATUS_LABELS[status]})"
        if cover_problem:
            return toast(request, f"Added {label}, but the cover couldn't be used: {cover_problem}",
                         error=True, changed=CHANGED)
        return toast(request, f"Added {label}", changed=CHANGED)

    @router.post("/shows/{show_id}/status")
    def change_status(request: Request, show_id: str, status: WatchStatus = Form()):
        try:
            handler.handle(ChangeShowStatus(show_id, status))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        entry = watchlist.get(show_id)
        return toast(request, f"{entry.title if entry else 'Title'} → {WATCH_STATUS_LABELS[status]}", changed=CHANGED)

    @router.post("/shows/{show_id}/genres")
    def set_genres(request: Request, show_id: str, genres: list[str] = Form([])):
        try:
            events = handler.handle(SetShowGenres(show_id, tuple(genres)))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        entry = watchlist.get(show_id)
        title = entry.title if entry else "Title"
        if not events:
            return toast(request, f"{title}: genres unchanged", changed=CHANGED)
        return toast(request, f"{title} · {', '.join(entry.genres) or 'no genres'}", changed=CHANGED)

    @router.delete("/shows/{show_id}")
    def remove_show(request: Request, show_id: str):
        try:
            [removed] = handler.handle(RemoveShow(show_id))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        return toast(request, f"Removed {removed.title}", changed=CHANGED)

    return router
