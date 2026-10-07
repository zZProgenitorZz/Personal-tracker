"""htmx-routes voor het leesdomein.

GET-routes geven HTML-fragmenten terug uit de read models. POST-routes sturen
dezelfde commands als de JSON-API en antwoorden met een toast plus het event
`reading-changed`, waarop de zichtbare onderdelen zichzelf opnieuw ophalen.
"""
from datetime import date, timedelta

from fastapi import APIRouter, File, Form, Request, UploadFile

from ..covers import CoverError, CoverStore
from ..web import render, save_cover, toast, templates
from .aggregate import DomainError
from ..cover_search import CoverSearch
from .commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, SetGenres, StartSeries
from .events import Kind, Status, StatusChanged
from .genres import GENRES
from .projections import LibraryEntry, LibraryProjection, ReadingActivityProjection

CHANGED = "reading-changed"

STATUS_LABELS = {
    Status.READING: "Reading",
    Status.ON_HOLD: "On hold",
    Status.COMPLETED: "Completed",
    Status.DROPPED: "Dropped",
}
KIND_LABELS = {Kind.MANHWA: "Manhwa", Kind.NOVEL: "Novel"}

templates.env.globals.update(STATUS_LABELS=STATUS_LABELS, KIND_LABELS=KIND_LABELS, GENRES=GENRES)


# ---- Gegevens voor de grafieken, afgeleid uit ReadingActivity ----

def last_days(per_day: dict[date, float], today: date, n: int) -> list[dict]:
    days = [today - timedelta(days=i) for i in reversed(range(n))]
    return [{"day": d, "label": d.strftime("%a")[0], "value": per_day.get(d, 0)} for d in days]


def last_weeks(per_day: dict[date, float], today: date, n: int) -> list[dict]:
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for i in reversed(range(n)):
        start = monday - timedelta(weeks=i)
        total = sum(per_day.get(start + timedelta(days=d), 0) for d in range(7))
        weeks.append({"start": start, "label": f"W{start.isocalendar()[1]}", "value": total})
    return weeks


def heatmap(per_day: dict[date, float], today: date, weeks: int) -> list[list[dict]]:
    """Kolommen van maandag t/m zondag, de laatste kolom is de huidige week."""
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


def with_height(bars: list[dict]) -> list[dict]:
    top = max((b["value"] for b in bars), default=0)
    for bar in bars:
        bar["height"] = round(100 * bar["value"] / top) if top else 0
    return bars


def reading_summary(library: LibraryProjection, activity: ReadingActivityProjection) -> dict:
    """Voor het startscherm en Settings."""
    today = date.today()
    current = library.currently_reading()
    this_week = sum(b["value"] for b in last_days(activity.per_day(), today, 7))
    return {
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

    def genre_counts() -> list[dict]:
        """Hoeveel series per genre, meeste eerst (alleen genres die voorkomen)."""
        counts = {g: sum(g in e.genres for e in library.all()) for g in GENRES}
        top = max(counts.values(), default=0)
        return [
            {"genre": g, "count": n, "width": round(100 * n / top) if top else 0}
            for g, n in sorted(counts.items(), key=lambda item: -item[1]) if n
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

    # Lezen

    @router.get("/dashboard")
    def dashboard(request: Request):
        today = date.today()
        return render(
            request, "dashboard.html",
            stats=overview(today),
            current=library.currently_reading()[:6],
            week=with_height(last_days(activity.per_day(), today, 7)),
            counts=status_counts(),
            library_size=len(library.all()),
        )

    @router.get("/library")
    def library_page(request: Request):
        return render(request, "library.html", entries=library.all(), status="", kind="", q="", genre="")

    @router.get("/library/grid")
    def library_grid(request: Request, status: str = "", kind: str = "", q: str = "", genre: str = ""):
        return render(
            request, "_library_grid.html",
            entries=filtered(status, kind, q, genre), status=status, kind=kind, q=q, genre=genre,
        )

    @router.get("/progress")
    def progress(request: Request):
        today = date.today()
        per_day = activity.per_day()
        weeks = with_height(last_weeks(per_day, today, 12))
        last_30 = [per_day.get(today - timedelta(days=i), 0) for i in range(30)]
        best = max(per_day.items(), key=lambda item: item[1], default=None)
        return render(
            request, "progress.html",
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
            genres=genre_counts(),
            library_size=len(library.all()),
        )

    # Schrijven

    @router.get("/covers/search")
    def find_cover(request: Request, title: str = "", index: int = 0):
        """Hulp bij het formulier: toont één zoekresultaat tegelijk. Slaat niets op."""
        if not title.strip():
            return render(request, "_cover_pick.html", error="Type a title first, then search for its cover.")
        try:
            results = cover_search.search(title)
        except CoverError as exc:
            return render(request, "_cover_pick.html", error=str(exc))
        if not results:
            return render(
                request, "_cover_pick.html",
                error=f"No covers found for “{title.strip()}”. Try the official title, or add one yourself.",
            )
        i = index % len(results)
        return render(
            request, "_cover_pick.html",
            pick=results[i], position=i + 1, total=len(results), next_index=(i + 1) % len(results),
        )

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
        # Eerst de cover. Mislukt die, dan wordt de serie toch opgeslagen.
        cover, cover_problem = None, None
        try:
            cover = save_cover(covers, cover_file, cover_link, cover_url)
        except CoverError as exc:
            cover_problem = str(exc)

        try:
            handler.handle(StartSeries(title, kind, source.strip(), start_chapter, cover, status, tuple(genres)))
        except DomainError as exc:
            if cover:
                covers.delete(cover)  # geen losse bestanden achterlaten
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
        """Inhoud van het venster "Edit genres", met de huidige genres aangevinkt."""
        entry = library.get(series_id)
        if entry is None:
            return toast(request, "Onbekende serie", error=True)
        return render(request, "_genre_editor.html", title=entry.title, options=GENRES, selected=entry.genres,
                      action=f"/ui/reading/series/{series_id}/genres")

    @router.get("/add-form")
    def add_form(request: Request):
        """Inhoud van het venster "Add series"."""
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
