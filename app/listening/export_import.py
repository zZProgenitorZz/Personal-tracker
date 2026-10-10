"""Je Spotify-geschiedenis importeren uit de Extended streaming history (een automation-slice).

Spotify levert op aanvraag JSON-bestanden met alles wat je ooit luisterde. Elk nummer gaat
als hetzelfde command als de live sync: RecordPlay, met source="export". De regels van de
aggregate blijven zoals ze zijn. Deze slice voegt alleen plays toe die Progen nog niet heeft,
ook in de periode van de live sync: zo vult een nieuwe export de gaten op die de sync miste
(Spotify geeft de sync hooguit de laatste 50 nummers).

Te bedienen vanuit Settings (Import streaming history), of vanaf de opdrachtregel:

    python -m app.listening.export_import <bestanden of map> [--dry-run]

Wat er overgeslagen wordt:
- records zonder spotify_track_uri: podcasts, audiobooks en lokale bestanden;
- plays korter dan MIN_MS_PLAYED (30 s), de grens die Spotify zelf gebruikt voor een "stream";
- plays die Progen al heeft: hetzelfde nummer binnen MATCH_WINDOW (30 s). De API geeft
  milliseconden en de export hele seconden, en ze kunnen een paar seconden verschillen, dus
  het bestaande "zelfde moment" is daarvoor niet genoeg.
Van een record bewaren we alleen wat in TrackPlayed staat; ip_addr, platform, land en de rest
gaan nergens heen. Een geüpload bestand wordt alleen gelezen, niet bewaard.
"""
import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..eventstore import EventStore
from .commands import ListeningCommandHandler, RecordPlay, as_utc
from .events import TrackPlayed

MIN_MS_PLAYED = 30_000
MATCH_WINDOW = timedelta(seconds=30)
BATCH = 2_000  # plays per transactie: snel, en de sync en de app kunnen ertussendoor
PROJECT = Path(__file__).resolve().parent.parent.parent

REASONS = {
    "no_track": "not a song (podcast, audiobook or local file)",
    "too_short": f"shorter than {MIN_MS_PLAYED // 1000} seconds",
    "already_known": "already in Progen",
    "unreadable": "unreadable record",
}


class ExportFileError(ValueError):
    """Een bestand dat geen Spotify streaming history is. De melding noemt het bestand."""


@dataclass(frozen=True)
class ExportFile:
    name: str
    records: list


def read_export(name: str, data: bytes) -> ExportFile:
    """Lees één exportbestand uit het geheugen. Herkent bestanden die er niet een zijn."""
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
    """Eén record als RecordPlay, of (None, reden) als hij overgeslagen wordt."""
    try:
        played_at = as_utc(datetime.fromisoformat(record["ts"]))  # het moment dat het nummer stopte
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
        artists=[artist] if artist else [],  # de export geeft maar één artiest
        album=record.get("master_metadata_album_album_name") or "", album_id="",  # zit niet in de export
        duration_ms=ms_played,  # de lengte zit niet in de export; minuten rekenen met ms_played
        ms_played=ms_played, source="export",
    ), None


class KnownPlays:
    """Alle plays die Progen al heeft, per nummer de gesorteerde tijden: zo is "zelfde nummer binnen
    30 seconden" snel te vinden, ook bij honderdduizenden plays."""

    def __init__(self):
        self._times: dict[str, list[datetime]] = defaultdict(list)

    @classmethod
    def from_store(cls, store: EventStore) -> "KnownPlays":
        known = cls()
        for event in store.load_type(TrackPlayed):
            known._times[event.track_id].append(as_utc(event.played_at))
        for times in known._times.values():
            times.sort()  # één keer sorteren is veel sneller dan elke play op zijn plek zetten
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
    """Wat een import zou doen; er is nog niets opgeslagen."""
    files: list[str] = field(default_factory=list)
    records: int = 0
    commands: list[RecordPlay] = field(default_factory=list)  # de nieuwe plays, klaar om op te slaan
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
    """Bepaal welke plays nieuw zijn. `known` wordt bijgewerkt, zodat dubbele in de export zelf
    (ook verspreid over bestanden) maar één keer meetellen."""
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
            moment = command.played_at.astimezone()  # lokale tijd, zoals de rest van Listening
            preview.first = min(preview.first or moment, moment)
            preview.last = max(preview.last or moment, moment)
            preview.artists.update(command.artists)
    preview.commands.sort(key=lambda c: c.played_at)
    return preview


def run_import(store: EventStore, commands: list[RecordPlay], progress=lambda done, total: None,
               batch: int = BATCH) -> int:
    """Sla de plays op, `batch` per transactie. Elk RecordPlay gaat langs de aggregate (zelfde moment =
    al bekend); de projecties horen het na elke portie (store.subscribe), dus geen herstart nodig.
    Geeft het aantal opgeslagen plays terug."""
    handler = ListeningCommandHandler(store)
    stored = 0
    for start in range(0, len(commands), batch):
        with store.transaction():
            for command in commands[start:start + batch]:
                if handler.handle(command):
                    stored += 1
        progress(min(start + batch, len(commands)), len(commands))
    return stored


# ---- Vanaf de opdrachtregel ----

def export_paths(paths: list[str]) -> list[Path]:
    found = []
    for path in map(Path, paths):
        found += sorted(path.glob("*.json")) if path.is_dir() else [path] if path.is_file() else []
    return found


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from ..backup import BackupError, Backups, default_backup_dir
    from ..desktop import is_running
    from ..main import EVENT_TYPES  # hier, om een kringverwijzing te voorkomen

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
    store = EventStore(args.db, EVENT_TYPES)
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
