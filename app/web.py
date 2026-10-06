"""Gedeelde onderdelen voor de htmx-webpagina: templates, filters en toasts.

De webpagina is een dunne laag bovenop dezelfde commands en read models als de
JSON-API. Elk domein heeft een eigen web.py met routes die HTML-fragmenten
teruggeven; deze module bevat wat ze delen.
"""
import zlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from .backup import KEEP_BACKUPS, BackupError, Backups, is_cloud_synced

STATIC = Path(__file__).parent / "static"
BACKUPS_CHANGED = "backups-changed"
templates = Jinja2Templates(directory=STATIC / "templates")


# ---- Filters voor in de templates ----

def chapter(value: float) -> str:
    """57.0 -> '57', 45.5 -> '45.5'"""
    return f"{value:g}"


def ago(moment: datetime) -> str:
    seconds = (datetime.now(timezone.utc) - moment).total_seconds()
    for unit, size in [("d", 86400), ("h", 3600), ("m", 60)]:
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def hue(text: str) -> int:
    """Een vaste kleurtoon per titel, binnen het paars-tot-teal palet."""
    return 160 + zlib.crc32(text.lower().encode()) % 140


def initials(title: str) -> str:
    """'Lord of the Mysteries' -> 'LM': kleine woorden als 'of' tellen niet mee."""
    words = [w for w in title.split() if w[:1].isalnum()]
    main = [w for w in words if w[0].isupper() or w[0].isdigit()] or words
    return "".join(w[0] for w in main[:2]).upper() or "?"


def nice_date(moment: datetime, seconds: bool = False) -> str:
    """'Mon 6 Oct 2026 · 21:05' (of '21:05:33' met seconds=True)"""
    time = f"{moment:%H:%M:%S}" if seconds else f"{moment:%H:%M}"
    return f"{moment:%a} {moment.day} {moment:%b %Y} · {time}"


templates.env.filters.update(chapter=chapter, ago=ago, hue=hue, initials=initials, nice_date=nice_date)

# Lijn-iconen (stijl van Lucide), te gebruiken via de macro ui.icon(naam).
ICONS = {
    "book": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    "image": '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "check-circle": '<path d="M21.8 10A10 10 0 1 1 17 3.3"/><path d="m9 11 3 3L22 4"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "flame": '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
    "layers": '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "trending": '<path d="M22 7 13.5 15.5 8.5 10.5 2 17"/><path d="M16 7h6v6"/>',
    "calendar": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    "trophy": '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
    "code": '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
    "grid": '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
    "tag": '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "alert":'<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
}
templates.env.globals["ICONS"] = ICONS


# ---- Antwoorden ----

def render(request: Request, name: str, **context):
    return templates.TemplateResponse(request, name, context)


def toast(request: Request, message: str, *, error: bool = False, changed: str | None = None):
    """Een melding rechtsonder. Met `changed` krijgen de onderdelen van de
    pagina die naar dat event luisteren een seintje om zich te verversen."""
    response = render(request, "_toast.html", message=message, error=error)
    response.headers["HX-Retarget"] = "#toasts"
    response.headers["HX-Reswap"] = "beforeend"
    if changed:
        response.headers["HX-Trigger"] = changed
    return response


# ---- Pagina's die niet bij één domein horen ----

def create_settings_router(db_path: str, series_count, backups: Backups) -> APIRouter:
    router = APIRouter(prefix="/ui", include_in_schema=False)

    def backup_context() -> dict:
        return {
            "backups": backups.list(), "backup_dir": str(backups.directory),
            "synced": is_cloud_synced(backups.directory), "keep": KEEP_BACKUPS,
        }

    @router.get("/settings")
    def settings(request: Request):
        location = db_path if db_path == ":memory:" else str(Path(db_path).resolve())
        return render(request, "settings.html", db_path=location, series_count=series_count(), **backup_context())

    @router.get("/backups")
    def backup_overview(request: Request):
        return render(request, "_backups.html", **backup_context())

    @router.post("/backups")
    def make_backup(request: Request):
        try:
            info = backups.create()
        except BackupError as exc:
            return toast(request, f"Backup failed: {exc}", error=True)
        return toast(request, f"Backup made · {info.events} events, {info.covers} covers", changed=BACKUPS_CHANGED)

    @router.post("/backups/{name}/restore")
    def restore_backup(request: Request, name: str):
        try:
            info = backups.restore(name)
        except BackupError as exc:
            return toast(request, f"Restore failed: {exc}", error=True)
        return toast(
            request, f"Restored the backup from {nice_date(info.created)}",
            changed=f"reading-changed, {BACKUPS_CHANGED}",
        )

    return router
