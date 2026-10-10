"""Import streaming history in Settings, en vanaf de opdrachtregel. Nep-exportbestanden, geen netwerk."""
import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.desktop as desktop
from app.eventstore import EventStore
from app.listening.events import TrackPlayed
from app.listening.export_import import main
from app.main import EVENT_TYPES, create_app


def record(ts, ms=200_000, track="Blinding Lights", artist="The Weeknd", uri="spotify:track:abc"):
    return {"ts": ts, "ms_played": ms, "master_metadata_track_name": track,
            "master_metadata_album_artist_name": artist, "master_metadata_album_album_name": "After Hours",
            "spotify_track_uri": uri, "episode_name": None, "platform": "android", "ip_addr": "203.0.113.7",
            "conn_country": "NL"}


def upload(name, records):
    return ("files", (name, json.dumps(records).encode(), "application/json"))


@pytest.fixture
def client(tmp_path):
    app = create_app(":memory:", backup_dir=tmp_path / "backups")
    client = TestClient(app, headers={"HX-Request": "true"})
    client.backup_dir = tmp_path / "backups"
    return client


def preview(client, *files):
    return client.post("/ui/listening/import/preview", files=list(files))


def wait_until_done(client, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = client.get("/ui/listening/import")
        if "Importing" not in response.text and "Making a backup" not in response.text:
            return response
        time.sleep(0.05)
    raise AssertionError("import didn't finish")


AUDIO = [record("2021-03-14T18:22:05Z"), record("2021-03-15T08:00:00Z", artist="Daft Punk", uri="spotify:track:d"),
         record("2021-03-15T09:00:00Z", ms=5_000), {**record("2021-03-15T10:00:00Z"), "spotify_track_uri": None}]


def test_settings_has_the_import_panel(client):
    assert 'hx-get="/ui/listening/import"' in client.get("/ui/settings").text
    panel = client.get("/ui/listening/import").text
    assert 'type="file"' in panel and "multiple" in panel and 'accept=".json"' in panel
    assert "Extended streaming history" in panel and "Streaming_History_Audio_" in panel


def test_choosing_files_shows_a_preview_and_stores_nothing(client):
    page = preview(client, upload("Streaming_History_Audio_2021.json", AUDIO)).text
    assert "4 records" in page and "Import 2 plays" in page
    assert "shorter than 30 seconds" in page and "not a song" in page
    assert "14 Mar 2021" in page and "15 Mar 2021" in page and "The Weeknd" in page and "Daft Punk" in page
    assert client.get("/listening/recent").json() == []


def test_an_unknown_file_is_named(client):
    page = preview(client, upload("Streaming_History_Audio_2021.json", AUDIO),
                   ("files", ("notes.json", b'{"hello": "world"}', "application/json"))).text
    assert "notes.json isn&#39;t a Spotify streaming history file" in page and "Import" not in page.split("notes.json")[1]


def test_import_runs_in_the_background_with_a_backup_first(client):
    preview(client, upload("Streaming_History_Audio_2021.json", AUDIO))
    started = client.post("/ui/listening/import/start")
    assert "every 1s" in started.text  # het paneel peilt de voortgang
    done = wait_until_done(client)
    assert "Imported 2 plays" in done.text and "toast" in done.text and "hx-swap-oob" in done.text
    assert "listening-changed" in done.headers["HX-Trigger"]
    # Meteen zichtbaar, zonder herstart: de projecties lopen live mee.
    assert [p["track"] for p in client.get("/listening/recent").json()] == ["Blinding Lights", "Blinding Lights"] or \
        len(client.get("/listening/recent").json()) == 2
    [backup] = list(client.backup_dir.iterdir())
    assert json.loads((backup / "info.json").read_text())["reason"] == "before-import"
    assert "saved automatically before an import" in client.get("/ui/backups").text
    # De toast komt maar één keer.
    assert "hx-swap-oob" not in client.get("/ui/listening/import").text


def test_importing_the_same_file_again_adds_nothing(client):
    preview(client, upload("a.json", AUDIO))
    client.post("/ui/listening/import/start")
    wait_until_done(client)
    client.post("/ui/listening/import/clear")
    page = preview(client, upload("a.json", AUDIO)).text
    assert "0 new" in page and "already in Progen" in page and "Import 0" not in page


def test_only_one_import_at_a_time(client, monkeypatch):
    import app.listening.import_web as import_web
    from threading import Event
    release = Event()
    real = import_web.run_import
    monkeypatch.setattr(import_web, "run_import", lambda *a, **kw: release.wait(5) and real(*a, **kw))
    preview(client, upload("a.json", AUDIO))
    client.post("/ui/listening/import/start")
    again = client.post("/ui/listening/import/start")
    assert "toast-error" in again.text
    assert "toast-error" in preview(client, upload("b.json", AUDIO)).text  # ook geen nieuw voorbeeld
    release.set()
    wait_until_done(client)


def test_the_uploaded_file_is_not_kept(client, tmp_path):
    preview(client, upload("Streaming_History_Audio_2021.json", AUDIO))
    client.post("/ui/listening/import/start")
    wait_until_done(client)
    assert not list(tmp_path.rglob("Streaming_History_Audio_2021.json"))


# ---- Opdrachtregel ----

@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "is_running", lambda: False)  # nooit echt naar 127.0.0.1:8000
    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "Streaming_History_Audio_2021.json").write_text(json.dumps(AUDIO), encoding="utf-8")
    return {"folder": folder, "db": tmp_path / "data" / "tracker.db", "backups": tmp_path / "backups",
            "covers": tmp_path / "data" / "covers"}


def run(paths, *args):
    return main([*args, "--db", str(paths["db"]), "--covers", str(paths["covers"]), "--to", str(paths["backups"])])


def test_command_line_dry_run(paths, capsys):
    assert run(paths, str(paths["folder"]), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "4 records in 1 file, 2 new" in out and "Nothing was saved" in out and not paths["backups"].exists()


def test_command_line_import_with_files_or_a_folder(paths, capsys, monkeypatch):
    assert run(paths, str(paths["folder"] / "Streaming_History_Audio_2021.json")) == 0
    out = capsys.readouterr().out
    assert "Imported 2 plays" in out and "Start Progen" in out
    assert len(EventStore(str(paths["db"]), EVENT_TYPES).load_type(TrackPlayed)) == 2
    assert list(paths["backups"].iterdir())  # eerst een back-up
    monkeypatch.setattr(desktop, "is_running", lambda: True)
    (paths["folder"] / "Streaming_History_Audio_2022.json").write_text(json.dumps([record("2022-01-01T08:00:00Z")]))
    assert run(paths, str(paths["folder"])) == 0
    out = capsys.readouterr().out
    assert "Imported 1 play." in out and "restart" in out


def test_command_line_with_a_wrong_file(paths, tmp_path, capsys):
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"not": "a list"}')
    assert run(paths, str(wrong)) == 1 and "wrong.json" in capsys.readouterr().err
    assert run(paths, str(tmp_path / "nowhere")) == 1
