"""Importing your Spotify history from the Extended streaming history (an automation slice).

On request, Spotify delivers JSON files with everything you ever listened to. Each track goes
through the same command as the live sync: RecordPlay, with source="export". The aggregate's
rules stay as they are. This slice only adds plays Progen doesn't have yet,
also in the period of the live sync: that way a new export fills the gaps the sync missed
(Spotify gives the sync at most the last 50 tracks).

Run it from Settings (Import streaming history), or from the command line:

    python -m app.listening.export_import <files or folder> [--dry-run]

What gets skipped:
- records without spotify_track_uri: podcasts, audiobooks and local files;
- plays shorter than MIN_MS_PLAYED (30 s), the limit Spotify itself uses for a "stream";
- plays Progen already has: the same track within MATCH_WINDOW (30 s). The API gives
  milliseconds and the export whole seconds, and they can differ by a few seconds, so
  the existing "same moment" isn't enough for that.
From a record we only keep what is in TrackPlayed; ip_addr, platform, country and the rest
go nowhere. An uploaded file is only read, never stored.
"""
import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..event_types import ALL_EVENT_TYPES
from ..eventstore import EventStore
from .commands import ListeningCommandHandler, RecordPlay, as_utc
from .events import TrackPlayed

MIN_MS_PLAYED = 30_000
MATCH_WINDOW = timedelta(seconds=30)
BATCH = 2_000  # plays per transaction: fast, and the sync and the app can get in between
PROJECT = Path(__file__).resolve().parent.parent.parent

REASONS = {
    "no_track": "not a song (podcast, audiobook or local file)",
    "too_short": f"shorter than {MIN_MS_PLAYED // 1000} seconds",
    "already_known": "already in Progen",
    "unreadable": "unreadable record",
}


class ExportFileError(ValueError):
    """A file that isn't Spotify streaming history. The message names the file."""


@dataclass(frozen=True)
class ExportFile:
    name: str
    records: list


def read_export(name: str, data: bytes) -> ExportFile:
    """Reads one export file from memory. Recognizes files that aren't one."""
    try:
        records = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ExportFileError(f"{name} isn't a JSON file.") from None
    if not isinstance(records, list):
        raise ExportFileError(f"{name} isn't a Spotify streaming history file (expected a list of plays).")
    if records and not (isinstance(records[0], dict) and "ts" in records[0] and "ms_played" in records[0]):
        raise ExportFileError(f"{name} isn't a Spotify streaming history file (no “ts” and “ms_played”).")
    return ExportFile(name, records)


def to_command(record: dict) -> tuple[RecordPlay | None, str | None]:
    """One record as RecordPlay, or (None, reason) if it is skipped."""
    try:
        played_at = as_utc(datetime.fromisoformat(record["ts"]))  # the moment the track stopped
        ms_played = int(record["ms_played"])
    except (KeyError, TypeError, ValueError):
        return None, "unreadable"
    uri = record.get("spotify_track_uri")
    if not uri:
        return None, "no_track"
    if ms_played < MIN_MS_PLAYED:
        return None, "too_short"
    artist = record.get("master_metadata_album_artist_name")
    return RecordPlay(
        played_at=played_at, track_id=uri, track=record.get("master_metadata_track_name") or "",
        artists=[artist] if artist else [],  # the export only gives one artist
        album=record.get("master_metadata_album_album_name") or "", album_id="",  # not in the export
        duration_ms=ms_played,  # the length isn't in the export; minutes are counted with ms_played
        ms_played=ms_played, source="export",
    ), None


class KnownPlays:
    """All plays Progen already has, per track the sorted times: that way "same track within
    30 seconds" is quick to find, even with hundreds of thousands of plays."""

    def __init__(self):
        self._times: dict[str, list[datetime]] = defaultdict(list)

    @classmethod
    def from_store(cls, store: EventStore) -> "KnownPlays":
        known = cls()
        for event in store.load_type(TrackPlayed):
            known._times[event.track_id].append(as_utc(event.played_at))
        for times in known._times.values():
            times.sort()  # sorting once is much faster than putting each play in its place
        return known

    def add(self, track_id: str, played_at: datetime) -> None:
        bisect.insort(self._times[track_id], played_at)

    def has(self, track_id: str, played_at: datetime) -> bool:
        times = self._times.get(track_id)
        if not times:
            return False
        i = bisect.bisect_left(times, played_at - MATCH_WINDOW)
        return i < len(times) and times[i] <= played_at + MATCH_WINDOW


@dataclass
class Preview:
    """What an import would do; nothing has been stored yet."""
    files: list[str] = field(default_factory=list)
    records: int = 0
    commands: list[RecordPlay] = field(default_factory=list)  # the new plays, ready to be stored
    skipped: Counter = field(default_factory=Counter)
    artists: Counter = field(default_factory=Counter)
    first: datetime | None = None
    last: datetime | None = None

    @property
    def new(self) -> int:
        return len(self.commands)

    def top_artists(self, n: int) -> list[tuple[str, int]]:
        return sorted(self.artists.items(), key=lambda item: (-item[1], item[0].lower()))[:n]

    def lines(self) -> list[str]:
        out = [f"{self.records} records in {len(self.files)} file{'s' if len(self.files) != 1 else ''}, "
               f"{self.new} new."]
        out += [f"  skipped {n}: {REASONS[reason]}" for reason, n in self.skipped.most_common()]
        if self.first:
            out.append(f"Period of the new plays: {self.first:%d %b %Y} – {self.last:%d %b %Y}")
        if self.artists:
            out.append("Top artists: " + ", ".join(f"{a} ({n})" for a, n in self.top_artists(5)))
        return out


def prepare(files: list[ExportFile], known: KnownPlays) -> Preview:
    """Decides which plays are new. `known` is updated, so duplicates within the export itself
    (also spread over files) only count once."""
    preview = Preview(files=[f.name for f in files])
    for export in files:
        for record in export.records:
            preview.records += 1
            command, reason = to_command(record) if isinstance(record, dict) else (None, "unreadable")
            if command is not None and known.has(command.track_id, command.played_at):
                command, reason = None, "already_known"
            if command is None:
                preview.skipped[reason] += 1
                continue
            known.add(command.track_id, command.played_at)
            preview.commands.append(command)
            moment = command.played_at.astimezone()  # local time, like the rest of Listening
            preview.first = min(preview.first or moment, moment)
            preview.last = max(preview.last or moment, moment)
            preview.artists.update(command.artists)
    preview.commands.sort(key=lambda c: c.played_at)
    return preview


def run_import(store: EventStore, commands: list[RecordPlay], progress=lambda done, total: None,
               batch: int = BATCH) -> int:
    """Stores the plays, `batch` per transaction. Each RecordPlay goes through the aggregate (same moment =
    already known); the projections hear about it after each portion (store.subscribe), so no restart is needed.
    Returns the number of stored plays."""
    handler = ListeningCommandHandler(store)
    stored = 0
    for start in range(0, len(commands), batch):
        with store.transaction():
            for command in commands[start:start + batch]:
                if handler.handle(command):
                    stored += 1
        progress(min(start + batch, len(commands)), len(commands))
    return stored


# ---- From the command line ----

def export_paths(paths: list[str]) -> list[Path]:
    found = []
    for path in map(Path, paths):
        found += sorted(path.glob("*.json")) if path.is_dir() else [path] if path.is_file() else []
    return found


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from ..backup import BackupError, Backups, default_backup_dir
    from ..desktop import is_running

    load_dotenv(PROJECT / ".env", override=False)
    parser = argparse.ArgumentParser(prog="python -m app.listening.export_import",
                                     description="Import your Spotify Extended streaming history into Progen.")
    parser.add_argument("paths", nargs="+", help="Streaming_History_Audio_*.json files, or a folder with them")
    parser.add_argument("--dry-run", action="store_true", help="only show what would be imported; save nothing")
    parser.add_argument("--db", default=str(PROJECT / "data" / "tracker.db"))
    parser.add_argument("--covers", default=str(PROJECT / "data" / "covers"))
    parser.add_argument("--to", default=None, help="backup folder (default: OneDrive\\Progen-backups)")
    args = parser.parse_args(argv)

    paths = export_paths(args.paths)
    if not paths:
        print("No export files (*.json) found.", file=sys.stderr)
        return 1
    try:
        files = [read_export(p.name, p.read_bytes()) for p in paths]
    except ExportFileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    store = EventStore(args.db, ALL_EVENT_TYPES)
    preview = prepare(files, KnownPlays.from_store(store))
    print("\n".join(preview.lines()))
    if args.dry_run:
        print("Nothing was saved (dry run).")
        return 0
    if not preview.new:
        print("Nothing new to import.")
        return 0
    try:
        backup = Backups(store, Path(args.covers), Path(args.to) if args.to else default_backup_dir()).create(
            reason="before-import")
    except BackupError as exc:
        print(f"Backup failed, nothing imported: {exc}", file=sys.stderr)
        return 1
    print(f"Backup made first: {backup.path}")
    stored = run_import(store, preview.commands,
                        progress=lambda done, total: print(f"  {done:,} / {total:,}".replace(",", " ")))
    print(f"Imported {stored} play{'' if stored == 1 else 's'}.")
    if is_running():
        print("Progen is running: restart it (Stop Progen, then start it again) to see the new plays.")
    else:
        print("Start Progen to see the new plays.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
