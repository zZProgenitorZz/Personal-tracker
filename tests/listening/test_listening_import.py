"""Importeren van de Spotify Extended streaming history. Alleen nep-exportbestanden, nooit echte."""
import json
from datetime import datetime, timezone

import pytest

import app.listening.import_export as importer
from app.eventstore import EventStore
from app.listening.commands import ListeningCommandHandler, RecordPlay
from app.listening.events import TrackPlayed
from app.listening.import_export import MIN_MS_PLAYED, import_folder, main, to_command
from app.listening.projections import ListeningActivityProjection, TopTracksProjection
from app.main import EVENT_TYPES


def record(ts="2021-03-14T18:22:05Z", ms=201_733, track="Blinding Lights", artist="The Weeknd",
           album="After Hours", uri="spotify:track:abc", **extra):
    values = {"ts": ts, "ms_played": ms, "master_metadata_track_name": track,
              "master_metadata_album_artist_name": artist, "master_metadata_album_album_name": album,
              "spotify_track_uri": uri, "episode_name": None, "reason_start": "trackdone",
              "reason_end": "trackdone", "skipped": False, "platform": "android", "ip_addr": "203.0.113.7",
              "conn_country": "NL"}
    return {**values, **extra}


def podcast(ts="2021-03-14T19:00:00Z"):
    return record(ts=ts, track=None, artist=None, album=None, uri=None, episode_name="Some podcast")


@pytest.fixture
def export(tmp_path):
    """Een map met nep-exportbestanden, zoals Spotify ze levert."""
    folder = tmp_path / "spotify"
    folder.mkdir()

    def write(name, records):
        (folder / name).write_text(json.dumps(records), encoding="utf-8")
    write.folder = folder
    return write


@pytest.fixture
def store():
    return EventStore(":memory:", EVENT_TYPES)


def plays(store):
    return store.load_type(TrackPlayed)


# ---- Vertaling naar RecordPlay ----

def test_a_record_becomes_the_existing_record_play():
    command, reason = to_command(record())
    assert reason is None
    assert command == RecordPlay(datetime(2021, 3, 14, 18, 22, 5, tzinfo=timezone.utc), "spotify:track:abc",
                                 "Blinding Lights", ["The Weeknd"], "After Hours", "", 201_733, 201_733, "export")


def test_nothing_else_from_the_export_is_stored(export, store):
    export("Streaming_History_Audio_2021.json", [record()])
    import_folder(export.folder, store)
    raw = "".join(row[0] for row in store._conn.execute("SELECT data FROM events"))
    assert "203.0.113.7" not in raw and "android" not in raw and "NL" not in raw and "trackdone" not in raw
    [play] = plays(store)
    assert (play.source, play.ms_played, play.duration_ms, play.album_id) == ("export", 201_733, 201_733, "")


def test_minutes_use_the_real_listening_time():
    # duration_ms = ms_played: de projecties rekenen met ms_played, dus niets wordt dubbel geteld.
    command, _ = to_command(record(ms=90_000))
    event = TrackPlayed(command.played_at, command.track_id, command.track, command.artists, command.album,
                        command.album_id, command.duration_ms, command.ms_played, command.source)
    activity, tracks = ListeningActivityProjection(), TopTracksProjection()
    activity.apply(event)
    tracks.apply(event)
    assert sum(activity.minutes_per_day().values()) == 1.5
    assert tracks.top(2021, 3)[0].minutes == 1.5


# ---- Wat overgeslagen wordt ----

@pytest.mark.parametrize("bad, reason", [
    (podcast(), "no_track"),
    (record(uri=None, track="Local file.mp3"), "no_track"),
    (record(ms=MIN_MS_PLAYED - 1), "too_short"),
    (record(ts="not a date"), "unreadable"),
    ({"ms_played": 1000}, "unreadable"),
])
def test_skipped_records(bad, reason):
    assert to_command(bad) == (None, reason)


def test_thirty_seconds_counts():
    command, reason = to_command(record(ms=MIN_MS_PLAYED))
    assert reason is None and command.ms_played == MIN_MS_PLAYED


def test_only_plays_from_before_the_first_live_sync_are_imported(export, store):
    # De live sync heeft vanaf zijn eerste play alles al (met milliseconden, dus een andere stream).
    ListeningCommandHandler(store).handle(RecordPlay(datetime(2024, 5, 1, 12, 0, 0, 123000, tzinfo=timezone.utc),
                                                     "spotify:track:live", "Live", ["X"], "A", "a", 180_000))
    export("Streaming_History_Audio_2024.json", [
        record(ts="2024-04-30T23:59:59Z", uri="spotify:track:before"),
        record(ts="2024-05-01T12:00:00Z", uri="spotify:track:live"),        # dezelfde play als de sync
        record(ts="2024-06-01T10:00:00Z", uri="spotify:track:after"),
    ])
    summary = import_folder(export.folder, store)
    assert summary.imported == 1 and summary.skipped["after_live_sync"] == 2
    assert sorted(p.track_id for p in plays(store)) == ["spotify:track:before", "spotify:track:live"]


def test_importing_twice_and_duplicates_in_the_export_add_nothing(export, store):
    export("Streaming_History_Audio_2021.json", [record(), record(), record(ts="2021-03-15T08:00:00Z")])
    export("Streaming_History_Audio_2021_1.json", [record()])  # ook in een ander bestand
    first = import_folder(export.folder, store)
    assert first.imported == 2 and first.skipped["already_in_progen"] == 2
    second = import_folder(export.folder, store)
    assert second.imported == 0 and second.skipped["already_in_progen"] == 4
    assert len(plays(store)) == 2


# ---- Dry run en overzicht ----

def test_dry_run_counts_but_stores_nothing(export, store):
    export("Streaming_History_Audio_2020.json", [
        record(ts="2020-01-02T10:00:00Z", artist="A"), record(ts="2020-01-03T10:00:00Z", artist="A"),
        record(ts="2020-02-01T10:00:00Z", artist="B"), record(ts="2020-02-01T10:00:00Z", artist="B"),
        record(ts="2020-03-01T10:00:00Z", artist="C", ms=5_000), podcast("2020-03-02T10:00:00Z"),
    ])
    summary = import_folder(export.folder, store, dry_run=True)
    assert store.count() == 0
    assert (summary.records, summary.imported) == (6, 3)
    assert dict(summary.skipped) == {"already_in_progen": 1, "too_short": 1, "no_track": 1}
    assert summary.first.date().isoformat() == "2020-01-02" and summary.last.date().isoformat() == "2020-02-01"
    assert summary.top_artists(5) == [("A", 2), ("B", 1)]


def test_unreadable_file_is_reported_not_fatal(export, store):
    (export.folder / "Streaming_History_Audio_broken.json").write_text("{not json", encoding="utf-8")
    export("Streaming_History_Audio_2021.json", [record()])
    summary = import_folder(export.folder, store)
    assert summary.imported == 1 and summary.broken_files == ["Streaming_History_Audio_broken.json"]


# ---- Vanaf de opdrachtregel ----

@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "is_running", lambda: False)  # nooit echt naar 127.0.0.1:8000
    return {"db": tmp_path / "data" / "tracker.db", "covers": tmp_path / "data" / "covers",
            "backups": tmp_path / "backups"}


def run(paths, *args):
    return main([*args, "--db", str(paths["db"]), "--covers", str(paths["covers"]), "--to", str(paths["backups"])])


def test_command_line_dry_run(export, paths, capsys):
    export("Streaming_History_Audio_2021.json", [record(), podcast()])
    assert run(paths, str(export.folder), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "2 records" in out and "1 to import" in out and "not a song" in out and "The Weeknd" in out
    assert "Nothing was saved" in out and not paths["backups"].exists()


def test_command_line_import_makes_a_backup_first_and_says_to_restart(export, paths, capsys, monkeypatch):
    export("Streaming_History_Audio_2021.json", [record(), record(ts="2021-03-15T08:00:00Z")])
    assert run(paths, str(export.folder)) == 0
    out = capsys.readouterr().out
    [backup] = list(paths["backups"].iterdir())
    assert json.loads((backup / "info.json").read_text())["reason"] == "before-import"
    assert "Streaming_History_Audio_2021.json: 2 imported" in out and "Start Progen" in out
    assert len(EventStore(str(paths["db"]), EVENT_TYPES).load_type(TrackPlayed)) == 2

    monkeypatch.setattr(importer, "is_running", lambda: True)
    export("Streaming_History_Audio_2022.json", [record(ts="2022-01-01T08:00:00Z")])
    run(paths, str(export.folder))
    assert "restart Progen" in capsys.readouterr().out


def test_command_line_without_anything_new_makes_no_backup(export, paths, capsys):
    export("Streaming_History_Audio_2021.json", [podcast()])
    assert run(paths, str(export.folder)) == 0
    assert "Nothing new to import" in capsys.readouterr().out and not paths["backups"].exists()


def test_command_line_with_a_missing_folder(paths, tmp_path, capsys):
    assert run(paths, str(tmp_path / "nowhere")) == 1
    assert "No export files" in capsys.readouterr().err
