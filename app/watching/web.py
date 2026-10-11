"""htmx routes for Watching: films, series and anime.

GET routes return HTML fragments from the read models. POST routes send
commands and answer with a toast plus the event `watching-changed`,
on which the visible parts fetch themselves again.
"""
from datetime import date

from fastapi import APIRouter, File, Form, Request, UploadFile

from .. import clock
from ..cover_search import CoverSearch
from ..charts import genre_bars, percent
from ..covers import CoverError, CoverStore, with_cover
from ..domain import DomainError
from ..web import Backlog, cover_search_response, pick_from_backlog, render, save_cover, templates, toast
from .commands import AddShow, ChangeShowStatus, RemoveShow, SetShowGenres, WatchingCommandHandler
from .events import WATCH_KIND_LABELS, WATCH_STATUS_LABELS, WatchKind, WatchStatus
from .genres import WATCH_GENRES
from .projections import WatchActivityProjection, WatchEntry, WatchlistProjection

CHANGED = "watching-changed"
# Gathering dust: a title on Watching that hasn't been added or changed status in this many days.
# Looser than for Reading, because Watching doesn't track episodes: a weaker signal.
STALE_WATCHING_DAYS = 30
DUST_SHOWN = 5  # after that "+N more"

templates.env.globals.update(
    WATCH_STATUS_LABELS=WATCH_STATUS_LABELS, WATCH_KIND_LABELS=WATCH_KIND_LABELS, WATCH_GENRES=WATCH_GENRES.names,
)


def finished_per_month(per_day: dict[date, int], today: date, months: int) -> list[dict]:
    """Finished per calendar month, the last `months` months (the current one last)."""
    result = []
    year, month = today.year, today.month
    for _ in range(months):
        result.append({"label": date(year, month, 1).strftime("%b"), "year": year, "month": month,
                       "value": sum(n for d, n in per_day.items() if (d.year, d.month) == (year, month))})
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    result.reverse()
    top = max((m["value"] for m in result), default=0)
    for m in result:
        m["height"] = percent(m["value"], top)
    return result


def watching_summary(watchlist: WatchlistProjection, activity: WatchActivityProjection) -> dict:
    """For the home screen and Settings."""
    today = clock.today()
    current = watchlist.currently_watching()
    dusty = len(watchlist.stale(clock.utc_now(), STALE_WATCHING_DAYS))
    this_month = sum(n for d, n in activity.per_day().items() if (d.year, d.month) == (today.year, today.month))
    return {
        "stats": [(len(current), "watching now"), (this_month, "finished this month"),
                  (len(watchlist.by_status(WatchStatus.COMPLETED)), "completed")],
        "current": current[:5],
        "count": len(watchlist.all()),
        "unit": "titles",
        "badge": f"{dusty} gathering dust" if dusty else None,
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

    def backlog() -> Backlog:
        return Backlog("watching", watchlist.by_status(WatchStatus.PLAN_TO_WATCH), lambda e: e.show_id,
                       "/ui/watching/shows/{}/status", WatchStatus.WATCHING.value, "Start watching", WATCH_GENRES.names)

    # Reading

    @router.get("/dashboard")
    def dashboard(request: Request):
        summary = watching_summary(watchlist, activity)
        now = clock.utc_now()
        return render(request, "watching_dashboard.html", summary=summary,
                      current=watchlist.currently_watching()[:6], counts=counts(),
                      dust=watchlist.stale(now, STALE_WATCHING_DAYS), dust_days=STALE_WATCHING_DAYS,
                      dust_shown=DUST_SHOWN, now=now)

    @router.get("/library")
    def library_page(request: Request):
        return render(request, "watching_library.html", entries=watchlist.all(), status="", kind="", genre="", q="",
                      backlog_size=len(watchlist.by_status(WatchStatus.PLAN_TO_WATCH)))

    @router.get("/pick-button")
    def pick_button(request: Request):
        return render(request, "_pick_button.html", tracker="watching",
                      count=len(watchlist.by_status(WatchStatus.PLAN_TO_WATCH)))

    @router.get("/pick")
    def pick(request: Request, exclude: str = "", genre: str = ""):
        """"What next?": a random title from Plan to Watch. Only reads; Start sends ChangeShowStatus."""
        return pick_from_backlog(request, backlog(), exclude, genre)

    @router.get("/library/grid")
    def library_grid(request: Request, status: str = "", kind: str = "", genre: str = "", q: str = ""):
        return render(request, "_watching_grid.html", entries=filtered(status, kind, genre, q),
                      status=status, kind=kind, genre=genre, q=q)

    @router.get("/progress")
    def progress(request: Request):
        per_day = activity.per_day()
        return render(
            request, "watching_progress.html",
            months=finished_per_month(per_day, clock.today(), 12),
            finished_total=sum(per_day.values()),
            summary=watching_summary(watchlist, activity),
            counts=counts(),
            genres=genre_bars(watchlist.all(), WATCH_GENRES.names),
        )

    @router.get("/add-form")
    def add_form(request: Request):
        return render(request, "_watching_add_form.html")

    @router.get("/covers/search")
    def find_cover(request: Request, title: str = "", index: int = 0):
        return cover_search_response(request, cover_search, title, index)

    @router.get("/shows/{show_id}/genres")
    def edit_genres(request: Request, show_id: str):
        entry = watchlist.get(show_id)
        if entry is None:
            return toast(request, "Unknown title", error=True)
        return render(request, "_genre_editor.html", title=entry.title, options=WATCH_GENRES.names,
                      selected=entry.genres, action=f"/ui/watching/shows/{show_id}/genres")

    # Writing

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
            with_cover(covers, cover, lambda: handler.handle(AddShow(title, kind, status, cover, tuple(genres))))
        except DomainError as exc:
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
