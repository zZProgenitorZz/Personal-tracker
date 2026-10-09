"""Gedeelde onderdelen voor de htmx-webpagina: templates, filters en toasts.

De webpagina is een dunne laag bovenop dezelfde commands en read models als de
JSON-API. Elk domein heeft een eigen web.py met routes die HTML-fragmenten
teruggeven; deze module bevat wat ze delen.
"""
import random
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.templating import Jinja2Templates

from .backup import KEEP_BACKUPS, BackupError, Backups, is_cloud_synced
from .covers import MAX_BYTES, CoverStore, too_large

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


def utc_now() -> datetime:
    """De klok voor de webpagina (tests zetten hem per tracker vooruit)."""
    return datetime.now(timezone.utc)


def quiet_for(moment: datetime, now: datetime) -> str:
    """Hoe lang iets al stil ligt, in gewone taal: '3 weeks ago', '2 months ago'."""
    days = (now - moment).days
    for unit, size, below in [("day", 1, 14), ("week", 7, 60), ("month", 30, 365), ("year", 365, None)]:
        if below is None or days < below:
            n = max(1, days // size)
            return f"{n} {unit}{'' if n == 1 else 's'} ago"


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


templates.env.filters.update(chapter=chapter, ago=ago, quiet_for=quiet_for, hue=hue, initials=initials, nice_date=nice_date)

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
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "film": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>',
    "tv": '<rect width="20" height="15" x="2" y="7" rx="2"/><path d="m17 2-5 5-5-5"/>',
    "star": '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "settings": '<path d="M21 4h-7"/><path d="M10 4H3"/><path d="M21 12h-9"/><path d="M8 12H3"/><path d="M21 20h-5"/><path d="M12 20H3"/><path d="M14 2v4"/><path d="M8 10v4"/><path d="M16 18v4"/>',
    "tag": '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "dice": '<rect width="18" height="18" x="3" y="3" rx="3"/><circle cx="8.5" cy="8.5" r="1" fill="currentColor"/><circle cx="15.5" cy="15.5" r="1" fill="currentColor"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
    "repeat": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "smartphone": '<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/>',
    "alert":'<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
}
templates.env.globals["ICONS"] = ICONS
# Welk icoon hoort bij welke soort (reading en watching samen).
templates.env.globals["KIND_ICONS"] = {"novel": "book", "manhwa": "image", "movie": "film", "series": "tv", "anime": "star"}


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


@dataclass
class Backlog:
    """Plan to Read/Watch van één tracker, voor de knop "Pick something" in de Library."""
    tracker: str                     # "reading": /ui/reading/pick
    entries: list                    # alleen de titels in de backlog
    item_id: Callable[[object], str]
    status_url: str                  # met {} voor het id; Start stuurt de bestaande statuswijziging
    start_status: str                # "reading" of "watching"
    start_label: str
    genre_order: list[str]


def pick_from_backlog(request: Request, backlog: Backlog, exclude: str = "", genre: str = ""):
    """Eén willekeurige titel uit de backlog, niet dezelfde als `exclude` (tenzij er maar één is).
    Een hulp bij het kiezen: leest alleen, geen command of event."""
    matching = [e for e in backlog.entries if not genre or genre in e.genres]
    others = [e for e in matching if backlog.item_id(e) != exclude]
    pick = random.choice(others or matching) if matching else None
    genres = [g for g in backlog.genre_order if any(g in e.genres for e in backlog.entries)]
    return render(request, "_pick.html", backlog=backlog, pick=pick, pick_id=pick and backlog.item_id(pick),
                  more=len(matching) > 1, genres=genres, genre=genre)


def save_cover(covers: CoverStore, upload: UploadFile | None, link: str, found: str) -> str | None:
    """Bewaar de gekozen cover lokaal. Volgorde: upload, geplakte link, zoekresultaat.
    Gooit CoverError als het niet lukt."""
    if upload is not None and upload.filename:
        data = upload.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise too_large()
        if data:
            return covers.save(data)
    url = link.strip() or found.strip()
    return covers.save_from_url(url) if url else None


# ---- Trackers ----

@dataclass(frozen=True)
class Tracker:
    """Wat het startscherm en Settings van een tracker moeten weten."""
    key: str                       # "reading": adres #reading en /ui/reading/...
    name: str                      # "Reading"
    icon: str                      # naam uit ICONS
    changed_event: str             # htmx-event na een wijziging, bv. "reading-changed"
    summary: Callable[[], dict]    # {"stats": [(waarde, label)], "covers": [...], "current": [...],
                                   #  "count": int, "unit": "series"}


# ---- Pagina's die niet bij één domein horen ----

def create_home_router(trackers: list[Tracker], wrapped: Callable[[], dict] | None = None) -> APIRouter:
    router = APIRouter(prefix="/ui", include_in_schema=False)

    @router.get("/home")
    def home(request: Request):
        hour = datetime.now().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        return render(request, "home.html", greeting=greeting,
                      trackers=[(t, t.summary()) for t in trackers], wrapped=wrapped() if wrapped else None)

    return router


def create_settings_router(db_path: str, trackers: list[Tracker], backups: Backups) -> APIRouter:
    router = APIRouter(prefix="/ui", include_in_schema=False)

    def backup_context() -> dict:
        return {
            "backups": backups.list(), "backup_dir": str(backups.directory),
            "synced": is_cloud_synced(backups.directory), "keep": KEEP_BACKUPS,
        }

    @router.get("/settings")
    def settings(request: Request):
        location = db_path if db_path == ":memory:" else str(Path(db_path).resolve())
        counts = [(t, t.summary()) for t in trackers]
        return render(request, "settings.html", db_path=location, counts=counts, **backup_context())

    # ---- Automatisch starten bij aanmelden in Windows ----

    @router.get("/autostart")
    def autostart_panel(request: Request):
        autostart = request.app.state.autostart
        return render(request, "_autostart.html", available=autostart.available, enabled=autostart.enabled)

    @router.post("/autostart")
    def set_autostart(request: Request, enabled: str = Form("")):
        # Dat dit van de app zelf komt, controleert app/security.py voor alle wijzigingen.
        autostart = request.app.state.autostart
        try:
            autostart.enable() if enabled else autostart.disable()
        except (RuntimeError, OSError) as exc:
            return toast(request, f"Couldn't change this: {exc}", error=True, changed="autostart-changed")
        message = ("Progen now starts when you sign in to Windows (in the background, with an icon by the clock)."
                   if enabled else "Progen no longer starts when you sign in.")
        return toast(request, message, changed="autostart-changed")

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
            # Eén back-up bevat alle trackers, dus alles ververst.
            changed=", ".join([*(t.changed_event for t in trackers), BACKUPS_CHANGED]),
        )

    return router
