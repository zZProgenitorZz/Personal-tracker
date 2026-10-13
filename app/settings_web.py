"""Settings: where the data lives, backups and restore, and starting with Windows."""
from pathlib import Path

from fastapi import APIRouter, Form, Request

from .backup import KEEP_BACKUPS, BackupError, Backups, is_cloud_synced
from .web import Tracker, nice_date, render, toast

BACKUPS_CHANGED = "backups-changed"


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
        auth = getattr(request.app.state, "auth", None)
        return render(request, "settings.html", db_path=location, counts=counts, **backup_context(),
                      extra_hosts=auth.extra_hosts if auth else [],
                      signed_in=getattr(request.state, "signed_in", False))

    # ---- Starting automatically when you sign in to Windows ----

    @router.get("/autostart")
    def autostart_panel(request: Request):
        autostart = request.app.state.autostart
        return render(request, "_autostart.html", available=autostart.available, enabled=autostart.enabled)

    @router.post("/autostart")
    def set_autostart(request: Request, enabled: str = Form("")):
        # app/security.py checks for every change that it comes from the app itself.
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
            # One backup holds all trackers, so everything refreshes.
            changed=", ".join([*(t.changed_event for t in trackers), BACKUPS_CHANGED]),
        )

    return router
