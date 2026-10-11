"""Import streaming history in Settings: choosing files, a preview, and the import in the background.

At most one import runs at a time (ImportJob). The files are only read in memory;
Starlette keeps an upload at most in a temporary file that is gone after the request.
Because the import goes through the server, the projections hear each portion live (store.subscribe):
no restart is needed. The panel polls the progress every second (htmx).
"""
import logging
import threading

from fastapi import APIRouter, File, Request, UploadFile

from ..backup import BackupError, Backups
from ..eventstore import EventStore
from ..web import render, toast
from .export_import import REASONS, ExportFileError, KnownPlays, Preview, prepare, read_export, run_import

log = logging.getLogger("progen.listening")
CHANGED = "listening-changed"


class ImportJob:
    """The state of the import: idle → preview → backup → running → done (or failed)."""

    def __init__(self, store: EventStore, backups: Backups):
        self._store = store
        self._backups = backups
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.state = "idle"
        self.preview: Preview | None = None
        self.problem: str | None = None   # e.g. a file that isn't an export
        self.done = self.total = self.stored = 0
        self.unreported = False           # the result hasn't been shown as a toast yet

    @property
    def busy(self) -> bool:
        return self.state in ("backup", "running")

    def make_preview(self, files: list[tuple[str, bytes]]) -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError("An import is already running.")
            self._reset()
            try:
                exports = [read_export(name, data) for name, data in files]
            except ExportFileError as exc:
                self.problem = str(exc)
                return
            self.preview = prepare(exports, KnownPlays.from_store(self._store))
            self.state = "preview"

    def start(self) -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError("An import is already running.")
            if self.state != "preview" or not self.preview.new:
                raise RuntimeError("Choose the export files first.")
            self.state, self.total, self.done = "backup", self.preview.new, 0
        threading.Thread(target=self._run, name="progen-import", daemon=True).start()

    def _run(self) -> None:
        try:
            self._backups.create(reason="before-import")
            self.state = "running"
            # Look again at what is already there: the sync may have added plays since the preview.
            known = KnownPlays.from_store(self._store)
            commands = [c for c in self.preview.commands if not known.has(c.track_id, c.played_at)]
            self.total = len(commands)
            self.stored = run_import(self._store, commands, progress=self._progress)
            self.state = "done"
        except BackupError as exc:
            self.problem, self.state = f"The backup failed, so nothing was imported: {exc}", "failed"
        except Exception as exc:  # never let the server fall over; do log it
            log.exception("Import of streaming history failed")
            self.problem, self.state = f"The import stopped: {exc}", "failed"
        self.unreported = True

    def _progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def clear(self) -> None:
        with self._lock:
            if not self.busy:
                self._reset()


def thousands(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def create_import_router(job: ImportJob) -> APIRouter:
    router = APIRouter(prefix="/ui/listening/import", include_in_schema=False)

    def panel(request: Request):
        response = render(request, "_spotify_import.html", job=job, reasons=REASONS, thousands=thousands,
                          report=None)
        if job.unreported and not job.busy:
            job.unreported = False
            ok = job.state == "done"
            message = (f"Imported {thousands(job.stored)} play{'s' if job.stored != 1 else ''} from your streaming history"
                       if ok else job.problem)
            response = render(request, "_spotify_import.html", job=job, reasons=REASONS, thousands=thousands,
                              report={"message": message, "error": not ok})
            if ok:
                response.headers["HX-Trigger"] = f"{CHANGED}, backups-changed"
        return response

    @router.get("")
    def show(request: Request):
        return panel(request)

    @router.post("/preview")
    def preview(request: Request, files: list[UploadFile] = File(...)):
        """Only reads and counts; nothing is stored yet."""
        contents = []
        for upload in files:
            try:
                contents.append((upload.filename or "file", upload.file.read()))
            finally:
                upload.file.close()  # any temporary file is gone right away then
        try:
            job.make_preview(contents)
        except RuntimeError as exc:
            return toast(request, str(exc), error=True)
        return panel(request)

    @router.post("/start")
    def start(request: Request):
        try:
            job.start()
        except RuntimeError as exc:
            return toast(request, str(exc), error=True)
        return panel(request)

    @router.post("/clear")
    def clear(request: Request):
        job.clear()
        return panel(request)

    return router
