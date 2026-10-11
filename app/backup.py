"""Backups of all your data: making, keeping and restoring them.

A backup is a folder named after the date and time, for example
`2026-10-06_21-05-33`, containing:

    tracker.db   a complete copy of the event store
    covers/      all covers
    info.json    when, how many events and covers, and why

Only the KEEP_BACKUPS newest are kept. By default they live in your
OneDrive (folder Progen-backups), so they are in the cloud too. Pick another
folder with PROGEN_BACKUP_DIR in .env.

Usable on its own, even when the app isn't running:
    python -m app.backup          make a backup
    python -m app.backup --list   show the backups
Restoring goes through Settings in the app.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .event_types import ALL_EVENT_TYPES
from .eventstore import EventStore

KEEP_BACKUPS = 10
BACKUP_DIR_ENV = "PROGEN_BACKUP_DIR"
PROJECT = Path(__file__).parent.parent
NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"
_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?")


class BackupError(Exception):
    """A backup that couldn't be made or restored. The message is for the user."""


@dataclass(frozen=True)
class BackupInfo:
    name: str
    path: Path
    created: datetime
    events: int | None
    covers: int | None
    reason: str  # "manual" or "before-restore"


def default_backup_dir() -> Path:
    """1. PROGEN_BACKUP_DIR, 2. OneDrive\\Progen-backups, 3. data/backups (only on this disk)."""
    if custom := os.environ.get(BACKUP_DIR_ENV, "").strip():
        return Path(custom)
    if onedrive := os.environ.get("OneDrive", "").strip():
        return Path(onedrive) / "Progen-backups"
    return PROJECT / "data" / "backups"


def is_cloud_synced(directory: Path) -> bool:
    onedrive = os.environ.get("OneDrive", "").strip()
    return bool(onedrive) and Path(onedrive).resolve() in [directory.resolve(), *directory.resolve().parents]


def _check_database(path: Path) -> int:
    """Opens the copy read-only and counts the events. Raises BackupError if it isn't sound."""
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("The backup is damaged")
            return connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise BackupError("The backup is damaged or isn't a Progen database") from exc


class Backups:
    def __init__(self, store: EventStore, covers_dir: Path, directory: Path, *,
                 keep: int = KEEP_BACKUPS, clock=datetime.now, on_restored=lambda: None):
        self._store = store
        self._covers = Path(covers_dir)
        self.directory = Path(directory)
        self._keep = keep
        self._clock = clock
        self._on_restored = on_restored  # rebuild the read models after a restore
        self._lock = threading.Lock()

    # ---- Making ----

    def create(self, reason: str = "manual") -> BackupInfo:
        with self._lock:
            info = self._create(reason)
            self._prune(keep_also=set())
            return info

    def _create(self, reason: str) -> BackupInfo:
        created = self._clock().replace(microsecond=0)
        name = self._free_name(created)
        # First in a temporary folder; only when everything checks out does it get its real name.
        partial = self.directory / f"{name}.partial"
        try:
            partial.mkdir(parents=True)
            self._store.copy_to(partial / "tracker.db")
            events = _check_database(partial / "tracker.db")
            covers = 0
            (partial / "covers").mkdir()
            if self._covers.is_dir():
                for cover in self._covers.glob("*.webp"):
                    shutil.copy2(cover, partial / "covers" / cover.name)
                    covers += 1
            (partial / "info.json").write_text(json.dumps({
                "app": "Progen", "created": created.isoformat(), "events": events,
                "covers": covers, "reason": reason,
            }, indent=2), encoding="utf-8")
            partial.rename(self.directory / name)
        except BackupError:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        except OSError as exc:
            shutil.rmtree(partial, ignore_errors=True)
            raise BackupError(f"Couldn't write the backup to {self.directory}: {exc.strerror or exc}") from exc
        return BackupInfo(name, self.directory / name, created, events, covers, reason)

    def _free_name(self, created: datetime) -> str:
        name, n = created.strftime(NAME_FORMAT), 1
        while (self.directory / name).exists():
            n += 1
            name = f"{created.strftime(NAME_FORMAT)}-{n}"
        return name

    def _prune(self, keep_also: set[str]) -> None:
        """Keeps at most KEEP_BACKUPS; the oldest go first, except those in keep_also."""
        backups = self.list()
        removable = [b for b in reversed(backups) if b.name not in keep_also]  # oldest first
        for old in removable[:max(0, len(backups) - self._keep)]:
            shutil.rmtree(old.path, ignore_errors=True)

    # ---- Viewing ----

    def list(self) -> list[BackupInfo]:
        """Newest first. Other folders in the backup folder are ignored."""
        if not self.directory.is_dir():
            return []
        found = []
        for path in self.directory.iterdir():
            if path.is_dir() and _NAME.fullmatch(path.name) and (path / "tracker.db").is_file():
                found.append(self._read_info(path))
        return sorted(found, key=lambda b: (b.created, b.name), reverse=True)

    def _read_info(self, path: Path) -> BackupInfo:
        try:
            info = json.loads((path / "info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        try:
            created = datetime.fromisoformat(info["created"])
        except (KeyError, TypeError, ValueError):
            created = datetime.strptime(path.name[:19], NAME_FORMAT)
        return BackupInfo(path.name, path, created, info.get("events"), info.get("covers"),
                          info.get("reason", "manual"))

    # ---- Restoring ----

    def restore(self, name: str) -> BackupInfo:
        with self._lock:
            chosen = next((b for b in self.list() if b.name == name), None)
            if chosen is None:
                raise BackupError("That backup doesn't exist (anymore)")
            _check_database(chosen.path / "tracker.db")  # check first, only then change anything

            self._create("before-restore")  # keep the current state, so you can go back
            self._store.restore_from(chosen.path / "tracker.db")
            self._covers.mkdir(parents=True, exist_ok=True)
            for cover in (chosen.path / "covers").glob("*.webp"):
                if not (self._covers / cover.name).exists():
                    shutil.copy2(cover, self._covers / cover.name)
            self._prune(keep_also={chosen.name})
            self._on_restored()
            return chosen


# ---- Standalone script ----

def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT / ".env", override=False)
    parser = argparse.ArgumentParser(prog="python -m app.backup", description="Back up Progen's data.")
    parser.add_argument("--list", action="store_true", help="show the backups instead of making one")
    parser.add_argument("--db", default=str(PROJECT / "data" / "tracker.db"))
    parser.add_argument("--covers", default=str(PROJECT / "data" / "covers"))
    parser.add_argument("--to", default=None, help="backup folder (default: OneDrive\\Progen-backups)")
    args = parser.parse_args(argv)

    if not args.list and not Path(args.db).exists():
        print(f"No database found at {args.db}", file=sys.stderr)
        return 1
    store = EventStore(args.db, ALL_EVENT_TYPES)
    backups = Backups(store, Path(args.covers), Path(args.to) if args.to else default_backup_dir())

    if args.list:
        for b in backups.list():
            print(f"{b.name}  {b.events} events, {b.covers} covers  ({b.reason})")
        return 0
    try:
        info = backups.create()
    except BackupError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Backup made: {info.path} ({info.events} events, {info.covers} covers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
