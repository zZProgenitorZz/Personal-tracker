"""Je hele Spotify-geschiedenis importeren uit de Extended streaming history (een automation-slice).

Spotify levert op aanvraag JSON-bestanden met alles wat je ooit luisterde. Deze importer
leest die bestanden en stuurt voor elk nummer hetzelfde command als de live sync:
RecordPlay, met source="export". De domeinlogica verandert niet.

    python -m app.listening.import_export              # map data/import/spotify/
    python -m app.listening.import_export <map>
    python -m app.listening.import_export --dry-run    # alleen tellen, niets opslaan

Werkt ook als de server niet draait. Draait hij wel, dan ziet hij de nieuwe plays pas
na een herstart (de projecties worden bij het starten opgebouwd).

Wat er overgeslagen wordt:
- records zonder spotify_track_uri: podcasts, audiobooks en lokale bestanden;
- plays korter dan MIN_MS_PLAYED (30 s), de grens die Spotify zelf gebruikt voor een "stream";
- plays vanaf de eerste live sync (source="api"): die heeft Progen al. De API geeft
  milliseconden en de export hele seconden, dus dezelfde play zou anders dubbel binnenkomen;
- plays die er al zijn (zelfde moment): dubbel in de export, of een tweede keer importeren.
Van een record bewaren we alleen wat in TrackPlayed staat; ip_addr, platform, land en de
rest gaan nergens heen.
"""
import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..desktop import is_running
from ..eventstore import EventStore
from .commands import ListeningCommandHandler, RecordPlay, as_utc, stream_id
from .events import TrackPlayed

PROJECT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FOLDER = PROJECT / "data" / "import" / "spotify"
MIN_MS_PLAYED = 30_000
# Een export-play telt als "al gesynct" als hij eindigt vanaf (eerste live play - deze marge). Veilig,
# want geïmporteerde plays duren minstens 30 s: twee echte plays eindigen nooit binnen 15 s van elkaar.
LIVE_SYNC_MARGIN = timedelta(seconds=15)

REASONS = {
    "no_track": "not a song (podcast, audiobook or local file)",
    "too_short": f"shorter than {MIN_MS_PLAYED // 1000} seconds",
    "after_live_sync": "from after your first live sync (Progen already has those)",
    "already_in_progen": "already in Progen (or twice in the export)",
    "unreadable": "unreadable record",
}


@dataclass
class Summary:
    records: int = 0
    imported: int = 0
    skipped: Counter = field(default_factory=Counter)
    first: datetime | None = None
    last: datetime | None = None
    artists: Counter = field(default_factory=Counter)
    broken_files: list[str] = field(default_factory=list)

    def count(self, command: RecordPlay) -> None:
        self.imported += 1
        moment = command.played_at.astimezone()  # lokale tijd, zoals de rest van Listening
        self.first = min(self.first or moment, moment)
        self.last = max(self.last or moment, moment)
        self.artists.update(command.artists)

    def top_artists(self, n: int) -> list[tuple[str, int]]:
        return sorted(self.artists.items(), key=lambda item: (-item[1], item[0].lower()))[:n]


def to_command(record: dict) -> tuple[RecordPlay | None, str | None]:
    """Eén record uit de export als RecordPlay, of (None, reden) als hij overgeslagen wordt."""
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
        duration_ms=ms_played,  # de lengte van het nummer zit niet in de export; minuten gaan via ms_played
        ms_played=ms_played, source="export",
    ), None


def export_files(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def first_live_play(store: EventStore) -> datetime | None:
    live = [e.played_at for e in store.load_type(TrackPlayed) if e.source == "api"]
    return min(live, default=None)


def import_folder(folder: Path, store: EventStore, dry_run: bool = False, progress=lambda line: None) -> Summary:
    """Lees alle exportbestanden in `folder`. Zonder dry_run worden de plays opgeslagen, per bestand in één
    transactie (elk RecordPlay gaat wel langs de aggregate). Met dry_run alleen tellen."""
    summary = Summary()
    handler = ListeningCommandHandler(store)
    live_from = first_live_play(store)
    cutoff = live_from - LIVE_SYNC_MARGIN if live_from else None
    seen: set[str] = set()  # voor dry_run: wat al in deze export voorbijkwam

    for path in export_files(folder):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("not a list")
        except (OSError, ValueError):
            summary.broken_files.append(path.name)
            progress(f"{path.name}: skipped, not a Spotify export file")
            continue
        imported_before, skipped_before = summary.imported, sum(summary.skipped.values())
        commands = []
        for record in records:
            summary.records += 1
            command, reason = to_command(record) if isinstance(record, dict) else (None, "unreadable")
            if command is not None and cutoff is not None and command.played_at >= cutoff:
                command, reason = None, "after_live_sync"
            if command is None:
                summary.skipped[reason] += 1
            else:
                commands.append(command)

        if dry_run:
            for command in commands:
                stream = stream_id(command.played_at)
                if stream in seen or store.load_stream(stream):
                    summary.skipped["already_in_progen"] += 1
                else:
                    seen.add(stream)
                    summary.count(command)
        else:
            with store.transaction():
                for command in commands:
                    if handler.handle(command):
                        summary.count(command)
                    else:
                        summary.skipped["already_in_progen"] += 1
        progress(f"{path.name}: {summary.imported - imported_before} {'to import' if dry_run else 'imported'}, "
                 f"{sum(summary.skipped.values()) - skipped_before} skipped")
    return summary


def report(summary: Summary, dry_run: bool) -> list[str]:
    lines = [f"{summary.records} records in the export, {summary.imported} "
             f"{'to import' if dry_run else 'imported'}."]
    for reason, n in summary.skipped.most_common():
        lines.append(f"  skipped {n}: {REASONS[reason]}")
    if summary.first:
        lines.append(f"Period: {summary.first:%d %b %Y} – {summary.last:%d %b %Y}")
    if summary.artists:
        lines.append("Top artists: " + ", ".join(f"{a} ({n})" for a, n in summary.top_artists(5)))
    return lines


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from ..backup import BackupError, Backups, default_backup_dir
    from ..main import EVENT_TYPES  # hier, om een kringverwijzing te voorkomen

    load_dotenv(PROJECT / ".env", override=False)
    parser = argparse.ArgumentParser(prog="python -m app.listening.import_export",
                                     description="Import your Spotify Extended streaming history into Progen.")
    parser.add_argument("folder", nargs="?", default=str(DEFAULT_FOLDER), help="folder with the export's JSON files")
    parser.add_argument("--dry-run", action="store_true", help="only count and show an overview; save nothing")
    parser.add_argument("--db", default=str(PROJECT / "data" / "tracker.db"))
    parser.add_argument("--covers", default=str(PROJECT / "data" / "covers"))
    parser.add_argument("--to", default=None, help="backup folder (default: OneDrive\\Progen-backups)")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    if not export_files(folder):
        print(f"No export files (*.json) found in {folder}", file=sys.stderr)
        return 1
    store = EventStore(args.db, EVENT_TYPES)

    # Eerst tellen; dat is meteen het overzicht van --dry-run.
    preview = import_folder(folder, store, dry_run=True)
    if args.dry_run:
        print("\n".join(report(preview, dry_run=True)))
        print("Nothing was saved (dry run).")
        return 0
    if preview.imported == 0:
        print("\n".join(report(preview, dry_run=True)))
        print("Nothing new to import.")
        return 0

    try:
        backup = Backups(store, Path(args.covers), Path(args.to) if args.to else default_backup_dir()).create(
            reason="before-import")
    except BackupError as exc:
        print(f"Backup failed, nothing imported: {exc}", file=sys.stderr)
        return 1
    print(f"Backup made first: {backup.path}")
    summary = import_folder(folder, store, progress=print)
    print("\n".join(report(summary, dry_run=False)))
    if is_running():
        print("Progen is running: restart Progen (Stop Progen, then start it again) to see the imported plays.")
    else:
        print("Start Progen to see the imported plays.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
