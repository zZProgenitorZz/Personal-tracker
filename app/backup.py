"""Back-ups van al je gegevens: maken, bewaren en terugzetten.

Een back-up is een map met datum en tijd als naam, bijvoorbeeld
`2026-10-06_21-05-33`, met daarin:

    tracker.db   een complete kopie van de event store
    covers/      alle covers
    info.json    wanneer, hoeveel events en covers, en waarom

Alleen de KEEP_BACKUPS nieuwste blijven bewaard. Standaard staan ze in je
OneDrive (map Progen-backups), zodat ze ook in de cloud staan. Een andere map
kies je met PROGEN_BACKUP_DIR in .env.

Los te gebruiken, ook als de app niet draait:
    python -m app.backup          maak een back-up
    python -m app.backup --list   toon de back-ups
Terugzetten gaat via Settings in de app.
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

from .eventstore import EventStore

KEEP_BACKUPS = 10
BACKUP_DIR_ENV = "PROGEN_BACKUP_DIR"
PROJECT = Path(__file__).parent.parent
NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"
_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?")


class BackupError(Exception):
    """Een back-up die niet gemaakt of teruggezet kon worden. De melding is voor de gebruiker."""


@dataclass(frozen=True)
class BackupInfo:
    name: str
    path: Path
    created: datetime
    events: int | None
    covers: int | None
    reason: str  # "manual" of "before-restore"


def default_backup_dir() -> Path:
    """1. PROGEN_BACKUP_DIR, 2. OneDrive\\Progen-backups, 3. data/backups (alleen op deze schijf)."""
    if custom := os.environ.get(BACKUP_DIR_ENV, "").strip():
        return Path(custom)
    if onedrive := os.environ.get("OneDrive", "").strip():
        return Path(onedrive) / "Progen-backups"
    return PROJECT / "data" / "backups"


def is_cloud_synced(directory: Path) -> bool:
    onedrive = os.environ.get("OneDrive", "").strip()
    return bool(onedrive) and Path(onedrive).resolve() in [directory.resolve(), *directory.resolve().parents]


def _check_database(path: Path) -> int:
    """Open de kopie alleen-lezen en tel de events. Gooit BackupError als hij niet deugt."""
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
        self._on_restored = on_restored  # read models opnieuw opbouwen na terugzetten
        self._lock = threading.Lock()

    # ---- Maken ----

    def create(self, reason: str = "manual") -> BackupInfo:
        with self._lock:
            info = self._create(reason)
            self._prune(keep_also=set())
            return info

    def _create(self, reason: str) -> BackupInfo:
        created = self._clock().replace(microsecond=0)
        name = self._free_name(created)
        # Eerst in een tijdelijke map; pas als alles klopt krijgt hij zijn echte naam.
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
        """Hooguit KEEP_BACKUPS bewaren; de oudste gaan eerst, behalve die in keep_also."""
        backups = self.list()
        removable = [b for b in reversed(backups) if b.name not in keep_also]  # oudste eerst
        for old in removable[:max(0, len(backups) - self._keep)]:
            shutil.rmtree(old.path, ignore_errors=True)

    # ---- Bekijken ----

    def list(self) -> list[BackupInfo]:
        """Nieuwste eerst. Andere mappen in de back-upmap worden genegeerd."""
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

    # ---- Terugzetten ----

    def restore(self, name: str) -> BackupInfo:
        with self._lock:
            chosen = next((b for b in self.list() if b.name == name), None)
            if chosen is None:
                raise BackupError("That backup doesn't exist (anymore)")
            _check_database(chosen.path / "tracker.db")  # eerst controleren, dan pas iets veranderen

            self._create("before-restore")  # de huidige stand bewaren, zodat je terug kunt
            self._store.restore_from(chosen.path / "tracker.db")
            self._covers.mkdir(parents=True, exist_ok=True)
            for cover in (chosen.path / "covers").glob("*.webp"):
                if not (self._covers / cover.name).exists():
                    shutil.copy2(cover, self._covers / cover.name)
            self._prune(keep_also={chosen.name})
            self._on_restored()
            return chosen


# ---- Los script ----

def main(argv: list[str] | None = None) -> int:
    from .main import EVENT_TYPES  # hier, om een kringverwijzing te voorkomen

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
    store = EventStore(args.db, EVENT_TYPES)
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
