"""Spotify Extended streaming history importeren: de kern. Alleen nep-exportbestanden, nooit echte."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.eventstore import EventStore
from app.listening.commands import ListeningCommandHandler, RecordPlay
from app.listening.events import TrackPlayed
from app.listening.export_import import (
    MATCH_WINDOW, MIN_MS_PLAYED, ExportFileError, KnownPlays, prepare, read_export, run_import, to_command,
)
from app.listening.projections import ListeningActivityProjection, RecentlyPlayedProjection, TopTracksProjection
from app.main import EVENT_TYPES

UTC = timezone.utc


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


def export_file(name, records):
    """Zoals een geüpload bestand: naam en inhoud (bytes)."""
    return name, json.dumps(records).encode("utf-8")


@pytest.fixture
def store():
    return EventStore(":memory:", EVENT_TYPES)


def live_play(store, when, track_id="spotify:track:abc"):
    """Een play van de live sync (API): tijd met milliseconden."""
    ListeningCommandHandler(store).handle(RecordPlay(when, track_id, "Blinding Lights", ["The Weeknd"],
                                                     "After Hours", "alb1", 200_000))


def import_files(store, *files):
    """Voorbeeld + import, zoals Settings het doet. Geeft (voorbeeld, aantal opgeslagen) terug."""
    preview = prepare([read_export(name, data) for name, data in files], KnownPlays.from_store(store))
    stored = run_import(store, preview.commands)
    return preview, stored


def plays(store):
    return store.load_type(TrackPlayed)


# ---- Vertaling naar RecordPlay ----

def test_a_record_becomes_the_existing_record_play():
    command, reason = to_command(record())
    assert reason is None
    assert command == RecordPlay(datetime(2021, 3, 14, 18, 22, 5, tzinfo=UTC), "spotify:track:abc",
                                 "Blinding Lights", ["The Weeknd"], "After Hours", "", 201_733, 201_733, "export")


def test_nothing_else_from_the_export_is_stored(store):
    import_files(store, export_file("Streaming_History_Audio_2021.json", [record()]))
    raw = "".join(row[0] for row in store._conn.execute("SELECT data FROM events"))
    assert "203.0.113.7" not in raw and "android" not in raw and '"NL"' not in raw and "trackdone" not in raw
    [play] = plays(store)
    assert (play.source, play.ms_played, play.duration_ms, play.album_id) == ("export", 201_733, 201_733, "")


def test_minutes_use_the_real_listening_time():
    # duration_ms = ms_played; alle minuten rekenen met ms_played (listened_ms), dus niets telt dubbel.
    command, _ = to_command(record(ms=90_000))
    event = TrackPlayed(command.played_at, command.track_id, command.track, command.artists, command.album,
                        command.album_id, command.duration_ms, command.ms_played, command.source)
    activity, tracks, recent = ListeningActivityProjection(), TopTracksProjection(), RecentlyPlayedProjection()
    for projection in (activity, tracks, recent):
        projection.apply(event)
    assert sum(activity.minutes_per_day().values()) == 1.5
    assert tracks.top(2021, 3)[0].minutes == 1.5 and recent.recent()[0].minutes == 1.5


# ---- Wat overgeslagen wordt ----

@pytest.mark.parametrize("bad, reason", [
    (podcast(), "no_track"),
    (record(uri=None, track="Local file.mp3"), "no_track"),
    (record(ms=MIN_MS_PLAYED - 1), "too_short"),
    (record(ts="not a date"), "unreadable"),
    ({"ts": "2021-03-14T18:22:05Z"}, "unreadable"),
])
def test_skipped_records(bad, reason):
    assert to_command(bad) == (None, reason)


def test_thirty_seconds_counts():
    assert to_command(record(ms=MIN_MS_PLAYED))[1] is None


@pytest.mark.parametrize("content", [b"{not json", b'{"ts": "2021"}', b'[{"song": "x"}]', b'["just text"]'])
def test_unknown_files_are_named(content):
    with pytest.raises(ExportFileError, match="Something.json"):
        read_export("Something.json", content)


def test_an_empty_export_file_is_fine():
    assert read_export("Streaming_History_Audio_2018.json", b"[]").records == []


# ---- Dubbel: zelfde nummer binnen 30 seconden ----

def test_a_play_the_sync_already_has_is_not_added_again(store):
    live_play(store, datetime(2024, 5, 1, 12, 0, 2, 734000, tzinfo=UTC))  # API: milliseconden, net anders
    preview, stored = import_files(store, export_file("a.json", [record(ts="2024-05-01T12:00:00Z")]))
    assert stored == 0 and preview.skipped == {"already_known": 1} and len(plays(store)) == 1


def test_the_window_is_thirty_seconds_and_per_track(store):
    live_play(store, datetime(2024, 5, 1, 11, 59, 59, 900000, tzinfo=UTC))  # API: met milliseconden
    _, stored = import_files(store, export_file("a.json", [
        record(ts="2024-05-01T12:00:29Z"),                                   # binnen 30 s: dubbel
        record(ts="2024-05-01T12:00:00Z", uri="spotify:track:other"),        # ander nummer: nieuw
        record(ts=(datetime(2024, 5, 1, 12, 0, 0) + MATCH_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")),  # 30,1 s: nieuw
    ]))
    assert stored == 2


def test_a_gap_in_the_live_sync_is_filled(store):
    # De pc stond uit: de sync mist de plays van 2 mei. De export heeft ze wel.
    live_play(store, datetime(2024, 5, 1, 20, 0, 0, 481000, tzinfo=UTC))
    live_play(store, datetime(2024, 5, 3, 20, 0, 1, 112000, tzinfo=UTC))
    _, stored = import_files(store, export_file("Streaming_History_Audio_2024.json", [
        record(ts="2024-05-01T20:00:00Z"), record(ts="2024-05-02T09:15:00Z"), record(ts="2024-05-02T09:20:00Z"),
        record(ts="2024-05-03T20:00:00Z"),
    ]))
    assert stored == 2
    assert sorted(p.played_at.date().isoformat() for p in plays(store)) == ["2024-05-01", "2024-05-02",
                                                                            "2024-05-02", "2024-05-03"]


def test_importing_twice_or_an_overlapping_newer_export_adds_nothing_double(store):
    old = export_file("Streaming_History_Audio_2021.json", [record(), record(ts="2021-03-15T08:00:00Z")])
    newer = export_file("Streaming_History_Audio_2021_1.json", [record(), record(ts="2021-03-15T08:00:00Z"),
                                                                record(ts="2021-03-16T08:00:00Z")])
    assert import_files(store, old)[1] == 2
    assert import_files(store, old)[1] == 0
    assert import_files(store, newer)[1] == 1
    assert len(plays(store)) == 3


def test_duplicates_inside_the_export_count_once(store):
    preview, stored = import_files(store, export_file("a.json", [record(), record()]),
                                   export_file("b.json", [record(ts="2021-03-14T18:22:10Z")]))
    assert stored == 1 and preview.skipped == {"already_known": 2}


# ---- Voorbeeld ----

def test_preview_counts_without_storing(store):
    files = [read_export("Streaming_History_Audio_2020.json", json.dumps([
        record(ts="2020-01-02T10:00:00Z", artist="A"), record(ts="2020-01-03T10:00:00Z", artist="A"),
        record(ts="2020-02-01T10:00:00Z", artist="B"), record(ts="2020-03-01T10:00:00Z", artist="C", ms=5_000),
        podcast("2020-03-02T10:00:00Z"),
    ]).encode())]
    preview = prepare(files, KnownPlays.from_store(store))
    assert store.count() == 0
    assert (preview.records, preview.new) == (5, 3)
    assert preview.skipped == {"too_short": 1, "no_track": 1}
    assert preview.first.date().isoformat() == "2020-01-02" and preview.last.date().isoformat() == "2020-02-01"
    assert preview.top_artists(5) == [("A", 2), ("B", 1)]


# ---- Opslaan: in porties, projecties live bijgewerkt ----

def test_import_stores_in_batches_with_progress_and_live_projections(store):
    recent = RecentlyPlayedProjection()
    store.subscribe(recent.apply)
    start = datetime(2022, 1, 1, tzinfo=UTC)
    commands = [to_command(record(ts=(start + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")))[0]
                for i in range(25)]
    progress = []
    stored = run_import(store, commands, progress=lambda done, total: progress.append((done, total)), batch=10)
    assert stored == 25 and progress == [(10, 25), (20, 25), (25, 25)]
    assert len(recent.recent()) == 25  # zonder herstart zichtbaar


def test_a_play_that_arrived_meanwhile_is_still_not_doubled(store):
    # Het voorbeeld is van vóór een sync; bij het opslaan gaat de aggregate-regel (zelfde moment) nog steeds voor.
    command, _ = to_command(record())
    ListeningCommandHandler(store).handle(command)
    assert run_import(store, [command]) == 0 and len(plays(store)) == 1
