"""Je Spotify-profiel (naam en foto) op het dashboard van Listening. Nep-Spotify, geen netwerk."""
import logging
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.listening.spotify import AVATAR_SIZE, PROFILE_REFRESH_SECONDS
from app.main import create_app


def png(size=(640, 480), color="orange") -> bytes:
    out = BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


class FakeSpotify:
    def __init__(self):
        self.profile = {"id": "me", "display_name": "Lenovo Listener", "images": [
            {"url": "https://i.scdn.co/image/small", "width": 64, "height": 64},
            {"url": "https://i.scdn.co/image/large", "width": 300, "height": 300},
        ]}
        self.profile_status = 200
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.host == "accounts.spotify.com":
            return httpx.Response(200, json={"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
        if request.url.path == "/v1/me":
            if self.profile_status != 200:
                return httpx.Response(self.profile_status)
            return httpx.Response(200, json=self.profile)
        if request.url.host == "i.scdn.co":
            return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})
        return httpx.Response(200, json={"items": []})  # recently-played


@pytest.fixture
def fake():
    return FakeSpotify()


@pytest.fixture
def setup(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.delenv("SPOTIFY_REDIRECT_URI", raising=False)
    http = httpx.Client(transport=httpx.MockTransport(fake))
    app = create_app(":memory:", http_client=http, data_dir=tmp_path, backup_dir=tmp_path / "backups")
    return TestClient(app, follow_redirects=False, headers={"HX-Request": "true"}), tmp_path


def connect(client):
    location = client.get("/listening/spotify/connect").headers["location"]
    state = parse_qs(urlsplit(location).query)["state"][0]
    client.get("/listening/spotify/callback", params={"code": "c", "state": state})


def test_profile_and_photo_are_fetched_when_connecting(setup, fake):
    client, data = setup
    connect(client)
    assert "/v1/me" in fake.paths and "/image/large" in fake.paths  # de grootste foto
    with Image.open(data / "spotify_avatar.webp") as avatar:
        assert avatar.format == "WEBP" and avatar.size == (AVATAR_SIZE, AVATAR_SIZE)  # vierkant bijgesneden


def test_dashboard_shows_name_and_photo(setup):
    client, _ = setup
    connect(client)
    page = client.get("/ui/listening/dashboard").text
    assert "Listening as" in page and "Lenovo Listener" in page
    assert 'src="/listening/spotify/avatar?v=' in page
    response = client.get("/listening/spotify/avatar")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"


def test_profile_without_photo_shows_initials(setup, fake):
    fake.profile["images"] = []
    client, data = setup
    connect(client)
    page = client.get("/ui/listening/dashboard").text
    assert "Lenovo Listener" in page and "/listening/spotify/avatar" not in page and ">LL<" in page
    assert not (data / "spotify_avatar.webp").exists()
    assert client.get("/listening/spotify/avatar").status_code == 404


def test_without_spotify_there_is_no_profile(setup):
    client, _ = setup
    assert "Listening as" not in client.get("/ui/listening/dashboard").text


def test_profile_error_never_blocks_connecting_or_syncing(setup, fake, caplog):
    fake.profile_status = 403
    client, data = setup
    with caplog.at_level(logging.WARNING, logger="progen.listening"):
        connect(client)
    assert (data / "spotify_token.json").exists()  # wel gekoppeld
    assert "profile" in caplog.text.lower()
    assert "toast-error" not in client.post("/ui/listening/spotify/sync").text
    assert "Listening as" not in client.get("/ui/listening/dashboard").text


def test_profile_is_refreshed_at_most_once_a_day(setup, fake, monkeypatch):
    client, _ = setup
    connect(client)
    client.post("/ui/listening/spotify/sync")
    client.post("/ui/listening/spotify/sync")
    assert fake.paths.count("/v1/me") == 1

    import app.listening.spotify as spotify_module
    real_time = spotify_module.time.time
    monkeypatch.setattr(spotify_module.time, "time", lambda: real_time() + PROFILE_REFRESH_SECONDS + 1)
    client.post("/ui/listening/spotify/sync")
    assert fake.paths.count("/v1/me") == 2


def test_new_name_shows_after_refresh(setup, fake, monkeypatch):
    client, _ = setup
    connect(client)
    fake.profile["display_name"] = "New Name"
    import app.listening.spotify as spotify_module
    real_time = spotify_module.time.time
    monkeypatch.setattr(spotify_module.time, "time", lambda: real_time() + PROFILE_REFRESH_SECONDS + 1)
    client.post("/ui/listening/spotify/sync")
    assert "New Name" in client.get("/ui/listening/dashboard").text


def test_disconnect_removes_profile_and_photo(setup):
    client, data = setup
    connect(client)
    client.post("/ui/listening/spotify/disconnect")
    assert not (data / "spotify_avatar.webp").exists() and not (data / "spotify_profile.json").exists()
    assert "Listening as" not in client.get("/ui/listening/dashboard").text


def test_profile_is_not_part_of_a_backup(setup):
    client, data = setup
    connect(client)
    client.post("/ui/backups")
    backed_up = [p.name for p in (data / "backups").rglob("*")]
    assert backed_up and not any(name.startswith("spotify") for name in backed_up)
