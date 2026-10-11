"""htmx routes for the reading domain.

GET routes return HTML fragments from the read models. POST routes send
the same commands as the JSON API and answer with a toast plus the event
`reading-changed`, on which the visible parts fetch themselves again.
"""
from datetime import date, timedelta

from fastapi import APIRouter, File, Form, Request, UploadFile

from .. import clock
from ..covers import CoverError, CoverStore, with_cover
from ..domain import DomainError
from ..charts import genre_bars, last_days, last_weeks, with_height
from ..web import Backlog, cover_search_response, pick_from_backlog, render, save_cover, toast, templates
from ..cover_search import CoverSearch
from .commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, SetGenres, StartSeries
from .events import KIND_LABELS, STATUS_LABELS, Kind, Status, StatusChanged
from .genres import GENRES
from .projections import STALE_READING_DAYS, LibraryEntry, LibraryProjection, ReadingActivityProjection

CHANGED = "reading-changed"
DUST_SHOWN = 5  # after that "+N more"

templates.env.globals.update(STATUS_LABELS=STATUS_LABELS, KIND_LABELS=KIND_LABELS, GENRES=GENRES)


# ---- Data for the charts, derived from ReadingActivity ----

def heatmap(per_day: dict[date, float], today: date, weeks: int) -> list[list[dict]]:
    """Columns from Monday to Sunday, the last column is the current week."""
    first = today - timedelta(days=today.weekday(), weeks=weeks - 1)
    top = max(per_day.values(), default=0)
    columns = []
    for w in range(weeks):
        column = []
        for d in range(7):
            day = first + timedelta(weeks=w, days=d)
            value = per_day.get(day, 0)
            level = 0 if not value or not top else min(4, 1 + int(3 * value / top))
            column.append({"day": day, "value": value, "level": level, "future": day > today})
        columns.append(column)
    return columns


def reading_summary(library: LibraryProjection, activity: ReadingActivityProjection) -> dict:
    """For the home screen and Settings."""
    today = clock.today()
    current = library.currently_reading()
    this_week = sum(b["value"] for b in last_days(activity.per_day(), today, 7))
    dusty = len(library.stale(clock.utc_now(), STALE_READING_DAYS))
    return {
        "badge": f"{dusty} gathering dust" if dusty else None,
        "stats": [(len(current), "reading now"), (this_week, "chapters this week"),
                  (activity.streak(today), "day streak")],
        "current": current[:5],
        "count": len(library.all()),
        "unit": "series",
    }


# ---- Routes ----

def create_reading_web_router(
    handler: ReadingCommandHandler,
    library: LibraryProjection,
    activity: ReadingActivityProjection,
    covers: CoverStore,
    cover_search: CoverSearch,
) -> APIRouter:
    router = APIRouter(prefix="/ui/reading", include_in_schema=False)

    def overview(today: date) -> dict:
        per_day = activity.per_day()
        return {
            "reading": len(library.by_status(Status.READING)),
            "completed": len(library.by_status(Status.COMPLETED)),
            "streak": activity.streak(today),
            "total": activity.total_chapters(),
            "this_week": sum(b["value"] for b in last_days(per_day, today, 7)),
        }

    def status_counts() -> list[dict]:
        entries = library.all()
        return [
            {"status": s, "label": label, "count": sum(e.status is s for e in entries)}
            for s, label in STATUS_LABELS.items()
        ]

    def filtered(status: str, kind: str, q: str, genre: str = "") -> list[LibraryEntry]:
        entries = library.all()
        if status in Status._value2member_map_:
            entries = [e for e in entries if e.status is Status(status)]
        if kind in Kind._value2member_map_:
            entries = [e for e in entries if e.kind is Kind(kind)]
        if q.strip():
            entries = [e for e in entries if q.strip().lower() in e.title.lower()]
        if genre:
            entries = [e for e in entries if genre in e.genres]
        return entries

    def backlog() -> Backlog:
        return Backlog("reading", library.by_status(Status.PLAN_TO_READ), lambda e: e.series_id,
                       "/ui/reading/series/{}/status", Status.READING.value, "Start reading", GENRES)

    # Reading

    @router.get("/dashboard")
    def dashboard(request: Request):
        today = clock.today()
        now = clock.utc_now()
        return render(
            request, "reading_dashboard.html",
            stats=overview(today),
            current=library.currently_reading()[:6],
            week=with_height(last_days(activity.per_day(), today, 7)),
            counts=status_counts(),
            library_size=len(library.all()),
            dust=library.stale(now, STALE_READING_DAYS), dust_days=STALE_READING_DAYS, dust_shown=DUST_SHOWN, now=now,
        )

    @router.get("/library")
    def library_page(request: Request):
        return render(request, "reading_library.html", entries=library.all(), status="", kind="", q="", genre="",
                      backlog_size=len(library.by_status(Status.PLAN_TO_READ)))

    @router.get("/pick-button")
    def pick_button(request: Request):
        return render(request, "_pick_button.html", tracker="reading", count=len(library.by_status(Status.PLAN_TO_READ)))

    @router.get("/pick")
    def pick(request: Request, exclude: str = "", genre: str = ""):
        """"What next?": a random series from Plan to Read. Only reads; Start sends ChangeStatus."""
        return pick_from_backlog(request, backlog(), exclude, genre)

    @router.get("/library/grid")
    def library_grid(request: Request, status: str = "", kind: str = "", q: str = "", genre: str = ""):
        return render(
            request, "_reading_grid.html",
            entries=filtered(status, kind, q, genre), status=status, kind=kind, q=q, genre=genre,
        )

    @router.get("/progress")
    def progress(request: Request):
        today = clock.today()
        per_day = activity.per_day()
        weeks = with_height(last_weeks(per_day, today, 12))
        last_30 = [per_day.get(today - timedelta(days=i), 0) for i in range(30)]
        best = max(per_day.items(), key=lambda item: item[1], default=None)
        return render(
            request, "reading_progress.html",
            stats=overview(today),
            weeks=weeks,
            avg_week=sum(w["value"] for w in weeks[-4:]) / 4,
            active_days=sum(1 for v in last_30 if v > 0),
            best=best,
            heatmap=heatmap(per_day, today, 26),
            counts=status_counts(),
            kinds=[
                {"label": label, "count": sum(e.kind is k for e in library.all())}
                for k, label in KIND_LABELS.items()
            ],
            genres=genre_bars(library.all(), GENRES),
            library_size=len(library.all()),
        )

    # Writing

    @router.get("/covers/search")
    def find_cover(request: Request, title: str = "", index: int = 0):
        return cover_search_response(request, cover_search, title, index)

    @router.post("/series")
    def start_series(
        request: Request,
        title: str = Form(),
        kind: Kind = Form(),
        source: str = Form(""),
        start_chapter: float = Form(0),
        status: Status = Form(Status.READING),
        genres: list[str] = Form([]),
        cover_file: UploadFile | None = File(None),
        cover_link: str = Form(""),
        cover_url: str = Form(""),
    ):
        # The cover first. If that fails, the series is saved anyway.
        cover, cover_problem = None, None
        try:
            cover = save_cover(covers, cover_file, cover_link, cover_url)
        except CoverError as exc:
            cover_problem = str(exc)

        try:
            with_cover(covers, cover, lambda: handler.handle(
                StartSeries(title, kind, source.strip(), start_chapter, cover, status, tuple(genres))))
        except DomainError as exc:
            return toast(request, str(exc), error=True)

        if cover_problem:
            return toast(
                request, f"Added {title.strip()} ({STATUS_LABELS[status]}), but the cover couldn't be used: {cover_problem}",
                error=True, changed=CHANGED,
            )
        added = f"Added {title.strip()}" + ("" if status is Status.READING else f" as {STATUS_LABELS[status]}")
        return toast(request, f"{added} to your library", changed=CHANGED)

    @router.post("/series/{series_id}/progress")
    def log_progress(request: Request, series_id: str, chapter: float = Form()):
        try:
            events = handler.handle(LogProgress(series_id, chapter))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        entry = library.get(series_id)
        message = f"{entry.title if entry else 'Series'} · chapter {chapter:g}"
        if any(isinstance(e, StatusChanged) for e in events):
            message += " · back to Reading"
        return toast(request, message, changed=CHANGED)

    @router.post("/series/{series_id}/status")
    def change_status(request: Request, series_id: str, status: Status = Form()):
        try:
            handler.handle(ChangeStatus(series_id, status))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        entry = library.get(series_id)
        return toast(request, f"{entry.title if entry else 'Series'} → {STATUS_LABELS[status]}", changed=CHANGED)

    @router.get("/series/{series_id}/genres")
    def edit_genres(request: Request, series_id: str):
        """Contents of the "Edit genres" window, with the current genres checked."""
        entry = library.get(series_id)
        if entry is None:
            return toast(request, "Unknown series", error=True)
        return render(request, "_genre_editor.html", title=entry.title, options=GENRES, selected=entry.genres,
                      action=f"/ui/reading/series/{series_id}/genres")

    @router.get("/add-form")
    def add_form(request: Request):
        """Contents of the "Add series" window."""
        return render(request, "_reading_add_form.html")

    @router.post("/series/{series_id}/genres")
    def set_genres(request: Request, series_id: str, genres: list[str] = Form([])):
        try:
            events = handler.handle(SetGenres(series_id, tuple(genres)))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        entry = library.get(series_id)
        title = entry.title if entry else "Series"
        if not events:
            return toast(request, f"{title}: genres unchanged", changed=CHANGED)
        chosen = ", ".join(entry.genres) if entry and entry.genres else "no genres"
        return toast(request, f"{title} · {chosen}", changed=CHANGED)

    @router.delete("/series/{series_id}")
    def remove_series(request: Request, series_id: str):
        try:
            [removed] = handler.handle(RemoveSeries(series_id))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        return toast(request, f"Removed {removed.title} from your library", changed=CHANGED)

    return router
