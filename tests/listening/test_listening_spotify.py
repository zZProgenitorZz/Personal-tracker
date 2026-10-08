"""De live koppeling met Spotify, tegen een nep-Spotify (httpx.MockTransport). Geen netwerk."""
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.eventstore import EventStore
from app.listening.commands import ListeningCommandHandler
from app.listening.events import TrackPlayed
from app.listening.projections import RecentlyPlayedProjection
from app.listening.spotify import (
    REDIRECT_URI, SCOPE, ListeningSync, NotConnected, RateLimited, Spotify, SpotifyError, TokenFile,
)
from app.main import create_app

T0 = 1_800_000_000.0  # nepklok, seconden


def item(played_at: str, name="Song", uri="spotify:track:1", duration_ms=200_000):
    return {"played_at": played_at, "track": {
        "uri": uri, "name": name, "duration_ms": duration_ms,
        "artists": [{"name": "Artist"}, {"name": "Guest"}], "album": {"name": "Album", "id": "alb1"}}}


class FakeSpotify:
    """Nep-accounts.spotify.com en api.spotify.com. Onthoudt alle verzoeken."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.items: list[dict] = []
        self.recent_status: list[int] = []      # statuscodes voor de volgende recently-played-verzoeken
        self.retry_after = "7"
        self.refresh_status = 200
        self.new_refresh_token = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "accounts.spotify.com":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(200, json={"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
            body = {"access_token": "A2", "expires_in": 3600}
            if self.new_refresh_token:
                body["refresh_token"] = self.new_refresh_token
            return httpx.Response(200, json=body)
        status = self.recent_status.pop(0) if self.recent_status else 200
        if status == 429:
            return httpx.Response(429, headers={"Retry-After": self.retry_after})
        if status != 200:
            return httpx.Response(status, json={"error": {"status": status}})
        return httpx.Response(200, json={"items": self.items})

    def recent_requests(self):
        return [r for r in self.requests if r.url.host == "api.spotify.com"]

    def token_requests(self):
        return [parse_qs(r.content.decode()) for r in self.requests if r.url.host == "accounts.spotify.com"]


@pytest.fixture
def fake():
    return FakeSpotify()


@pytest.fixture
def clock():
    return [T0]


def make_spotify(tmp_path, fake, clock, token=None):
    tokens = TokenFile(tmp_path / "spotify_token.json")
    if token:
        tokens.save(token)
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Spotify(http, tokens, "client-id", "client-secret", clock=lambda: clock[0]), tokens


def make_sync(tmp_path, fake, clock, token=None):
    spotify, tokens = make_spotify(tmp_path, fake, clock, token)
    store = EventStore(":memory:", [TrackPlayed])
    recent = RecentlyPlayedProjection()
    store.subscribe(recent.apply)
    sync = ListeningSync(spotify, ListeningCommandHandler(store), recent, tmp_path / "spotify_sync.json",
                         interval_seconds=1800)
    return sync, spotify, store, recent


VALID = {"access_token": "A1", "refresh_token": "R1", "expires_at": T0 + 3600}


# ---- Inloggen (Authorization Code flow) ----

def test_authorize_url_asks_only_for_recently_played_with_a_state(tmp_path, fake, clock):
    spotify, _ = make_spotify(tmp_path, fake, clock)
    url = urlsplit(spotify.authorize_url())
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.spotify.com" and url.path == "/authorize"
    assert query["client_id"] == "client-id" and query["response_type"] == "code"
    assert query["scope"] == SCOPE == "user-read-recently-played"
    assert query["redirect_uri"] == REDIRECT_URI == "http://127.0.0.1:8000/listening/spotify/callback"
    assert len(query["state"]) >= 20
    assert parse_qs(urlsplit(spotify.authorize_url()).query)["state"][0] != query["state"]


def test_wrong_state_is_refused_and_nothing_is_saved(tmp_path, fake, clock):
    spotify, tokens = make_spotify(tmp_path, fake, clock)
    spotify.authorize_url()
    with pytest.raises(SpotifyError, match="state"):
        spotify.finish_authorization("code", "verzonnen")
    assert tokens.load() is None and fake.requests == []


def test_expired_state_is_refused(tmp_path, fake, clock):
    spotify, _ = make_spotify(tmp_path, fake, clock)
    state = parse_qs(urlsplit(spotify.authorize_url()).query)["state"][0]
    clock[0] += 3600
    with pytest.raises(SpotifyError):
        spotify.finish_authorization("code", state)


def test_correct_state_exchanges_the_code_once(tmp_path, fake, clock):
    spotify, tokens = make_spotify(tmp_path, fake, clock)
    state = parse_qs(urlsplit(spotify.authorize_url()).query)["state"][0]
    spotify.finish_authorization("the-code", state)

    [form] = fake.token_requests()
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
    assert form["redirect_uri"] == [REDIRECT_URI]
    auth = fake.requests[0].headers["authorization"]
    assert auth == "Basic " + base64.b64encode(b"client-id:client-secret").decode()
    assert tokens.load() == {"access_token": "A1", "refresh_token": "R1", "expires_at": T0 + 3600}
    assert spotify.connected
    with pytest.raises(SpotifyError):
        spotify.finish_authorization("the-code", state)  # een state werkt maar één keer


def test_not_configured_without_client_id(tmp_path, fake, clock):
    spotify = Spotify(httpx.Client(transport=httpx.MockTransport(fake)), TokenFile(tmp_path / "t.json"), "", "")
    assert not spotify.configured
    with pytest.raises(SpotifyError, match="SPOTIFY_CLIENT_ID"):
        spotify.authorize_url()


# ---- Tokens verversen ----

def test_expired_access_token_is_refreshed_first(tmp_path, fake, clock):
    spotify, tokens = make_spotify(tmp_path, fake, clock, {**VALID, "expires_at": T0 - 1})
    spotify.recently_played(None)
    assert fake.token_requests()[0]["grant_type"] == ["refresh_token"]
    assert fake.token_requests()[0]["refresh_token"] == ["R1"]
    assert fake.recent_requests()[0].headers["authorization"] == "Bearer A2"
    assert tokens.load()["refresh_token"] == "R1"  # Spotify stuurt niet altijd een nieuwe mee


def test_new_refresh_token_is_kept(tmp_path, fake, clock):
    fake.new_refresh_token = "R2"
    spotify, tokens = make_spotify(tmp_path, fake, clock, {**VALID, "expires_at": T0 - 1})
    spotify.recently_played(None)
    assert tokens.load()["refresh_token"] == "R2"


def test_401_refreshes_once_and_retries(tmp_path, fake, clock):
    fake.recent_status = [401, 200]
    spotify, _ = make_spotify(tmp_path, fake, clock, VALID)
    spotify.recently_played(None)
    assert [r.headers["authorization"] for r in fake.recent_requests()] == ["Bearer A1", "Bearer A2"]


def test_revoked_access_disconnects(tmp_path, fake, clock):
    fake.refresh_status = 400
    spotify, tokens = make_spotify(tmp_path, fake, clock, {**VALID, "expires_at": T0 - 1})
    with pytest.raises(SpotifyError, match="connect"):
        spotify.recently_played(None)
    assert tokens.load() is None and not spotify.connected


def test_without_token_nothing_is_asked(tmp_path, fake, clock):
    spotify, _ = make_spotify(tmp_path, fake, clock)
    with pytest.raises(NotConnected):
        spotify.recently_played(None)
    assert fake.requests == []


def test_429_tells_how_long_to_wait(tmp_path, fake, clock):
    fake.recent_status = [429]
    spotify, _ = make_spotify(tmp_path, fake, clock, VALID)
    with pytest.raises(RateLimited) as limited:
        spotify.recently_played(None)
    assert limited.value.retry_after == 7


# ---- Synchroniseren ----

def test_sync_records_plays_from_spotify(tmp_path, fake, clock):
    fake.items = [item("2026-10-08T12:00:00.000Z", "Second", "spotify:track:2"),
                  item("2026-10-08T11:56:00.000Z", "First")]
    sync, _, store, recent = make_sync(tmp_path, fake, clock, VALID)

    result = sync.run()

    assert (result.stored, result.skipped) == (2, 0)
    first = store.load_all()[0]
    assert first.track_id in {"spotify:track:1", "spotify:track:2"} and first.source == "api"
    assert first.artists == ["Artist", "Guest"] and first.album_id == "alb1" and first.ms_played is None
    assert recent.recent()[0].track == "Second"
    params = fake.recent_requests()[0].url.params
    assert params["limit"] == "50" and "after" not in params


def test_sync_asks_only_for_plays_after_the_latest(tmp_path, fake, clock):
    fake.items = [item("2026-10-08T12:00:00.000Z")]
    sync, *_ = make_sync(tmp_path, fake, clock, VALID)
    sync.run()
    sync.run()  # Spotify geeft dezelfde play nog eens (overlap): overgeslagen
    after = int(datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp() * 1000)
    assert fake.recent_requests()[1].url.params["after"] == str(after)
    assert sync.status()["skipped"] == 1 and sync.status()["stored"] == 0


def test_sync_skips_items_without_a_track_and_invalid_ones(tmp_path, fake, clock):
    fake.items = [{"played_at": "2026-10-08T12:00:00Z", "track": None},
                  item("2026-10-08T12:04:00Z", duration_ms=-5),
                  item("2026-10-08T12:08:00Z")]
    sync, _, store, _ = make_sync(tmp_path, fake, clock, VALID)
    result = sync.run()
    assert (result.stored, result.skipped) == (1, 2) and len(store.load_all()) == 1


def test_sync_status_is_remembered_across_restarts(tmp_path, fake, clock):
    fake.items = [item("2026-10-08T12:00:00Z")]
    sync, *_ = make_sync(tmp_path, fake, clock, VALID)
    sync.run()
    again, *_ = make_sync(tmp_path, fake, clock)
    assert again.status()["stored"] == 1 and again.status()["error"] is None
    assert json.loads((tmp_path / "spotify_sync.json").read_text())["last_sync_at"]


# ---- De achtergrondtaak (één tik van de lus) ----

def test_tick_without_token_does_nothing(tmp_path, fake, clock):
    sync, *_ = make_sync(tmp_path, fake, clock)
    assert sync.tick() == 1800 and fake.requests == []


def test_tick_waits_for_retry_after_on_429(tmp_path, fake, clock):
    fake.recent_status = [429]
    sync, *_ = make_sync(tmp_path, fake, clock, VALID)
    assert sync.tick() == 7


def test_tick_logs_errors_and_never_raises(tmp_path, fake, clock, caplog):
    fake.recent_status = [500]
    sync, *_ = make_sync(tmp_path, fake, clock, VALID)
    with caplog.at_level(logging.WARNING, logger="progen.listening"):
        assert sync.tick() == 1800
    assert "Spotify sync failed" in caplog.text and sync.status()["error"]


def test_tick_survives_unexpected_crashes(tmp_path, fake, clock, caplog, monkeypatch):
    sync, *_ = make_sync(tmp_path, fake, clock, VALID)
    monkeypatch.setattr(sync, "run", lambda: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="progen.listening"):
        assert sync.tick() == 1800
    assert "ZeroDivisionError" in caplog.text


# ---- In de app: Settings, connect, callback ----

@pytest.fixture
def app_client(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.delenv("SPOTIFY_REDIRECT_URI", raising=False)
    http = httpx.Client(transport=httpx.MockTransport(fake))
    app = create_app(":memory:", http_client=http, data_dir=tmp_path, backup_dir=tmp_path / "backups")
    return TestClient(app, follow_redirects=False), tmp_path


def connect(client):
    response = client.get("/listening/spotify/connect")
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    return response, client.get("/listening/spotify/callback", params={"code": "c", "state": state})


def test_connect_and_callback(app_client, fake):
    client, data = app_client
    fake.items = [item("2026-10-08T12:00:00Z")]
    start, done = connect(client)
    assert start.status_code == 307 and start.headers["location"].startswith("https://accounts.spotify.com/authorize")
    assert done.status_code == 303 and done.headers["location"] == "/#settings"
    assert (data / "spotify_token.json").exists()
    assert len(client.get("/listening/recent").json()) == 1  # meteen een eerste sync


def test_callback_with_wrong_state_is_refused(app_client):
    client, data = app_client
    client.get("/listening/spotify/connect")
    response = client.get("/listening/spotify/callback", params={"code": "c", "state": "nep"})
    assert response.status_code == 400 and not (data / "spotify_token.json").exists()


def test_callback_when_you_said_no(app_client):
    client, _ = app_client
    response = client.get("/listening/spotify/callback", params={"error": "access_denied", "state": "x"})
    assert response.status_code == 400 and "access_denied" in response.text


def test_settings_panel_buttons(app_client):
    client, _ = app_client
    assert "Connect Spotify" in client.get("/ui/listening/spotify").text
    connect(client)
    panel = client.get("/ui/listening/spotify").text
    assert "Sync now" in panel and "Disconnect" in panel and "Last sync" in panel
    assert 'hx-get="/ui/listening/spotify"' in client.get("/ui/settings").text


def test_sync_now_and_disconnect(app_client, fake):
    client, data = app_client
    connect(client)
    fake.items = [item("2026-10-08T13:00:00Z", "New")]
    response = client.post("/ui/listening/spotify/sync")
    assert "1 new play" in response.text
    assert "listening-changed" in response.headers["HX-Trigger"]
    response = client.post("/ui/listening/spotify/disconnect")
    assert "spotify-changed" in response.headers["HX-Trigger"] and not (data / "spotify_token.json").exists()
    assert "Connect Spotify" in client.get("/ui/listening/spotify").text


def test_sync_now_reports_rate_limit(app_client, fake):
    client, _ = app_client
    connect(client)
    fake.recent_status = [429]
    assert "try again in 7" in client.post("/ui/listening/spotify/sync").text


def test_settings_explains_setup_without_client_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "")
    client = TestClient(create_app(":memory:", data_dir=tmp_path))
    panel = client.get("/ui/listening/spotify").text
    assert "SPOTIFY_CLIENT_ID" in panel and "Connect Spotify" not in panel
    assert client.get("/listening/spotify/connect").status_code == 400


def test_background_sync_starts_and_stops_with_the_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    with TestClient(create_app(":memory:", data_dir=tmp_path)) as client:
        assert client.get("/ui/home").status_code == 200


def test_token_is_not_part_of_a_backup(app_client):
    client, data = app_client
    connect(client)
    client.post("/ui/backups")
    backups = [p for p in data.rglob("*") if "backups" in p.parts]
    assert backups and not any(p.name.startswith("spotify") for p in backups)
