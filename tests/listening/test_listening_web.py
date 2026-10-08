"""Listening via de JSON-API, de webpagina, het startscherm en Settings."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def body(played_at=NOW, **fields) -> dict:
    values = {"played_at": played_at.isoformat(), "track_id": "spotify:track:abc", "track": "Blinding Lights",
              "artists": ["The Weeknd"], "album": "After Hours", "album_id": "alb1", "duration_ms": 180_000}
    return {**values, **fields}


@pytest.fixture
def client():
    return TestClient(create_app(":memory:"))


# ---- JSON-API ----

def test_post_play_stores_it_once(client):
    first = client.post("/listening/plays", json=body())
    again = client.post("/listening/plays", json=body())
    assert (first.status_code, first.json()["stored"]) == (201, True)
    assert (again.status_code, again.json()["stored"]) == (200, False)
    assert first.json()["stream_id"].startswith("play-")
    assert len(client.get("/listening/recent").json()) == 1


def test_post_invalid_play(client):
    assert client.post("/listening/plays", json=body(duration_ms=-1)).status_code == 400
    assert client.post("/listening/plays", json=body(track_id="")).status_code == 400
    assert client.post("/listening/plays", json={"track": "zonder tijd"}).status_code == 422


def test_get_endpoints_read_the_projections(client):
    client.post("/listening/plays", json=body())
    client.post("/listening/plays", json=body(NOW - timedelta(minutes=5), artists=["The Weeknd", "Daft Punk"],
                                              track_id="spotify:track:other", track="Starboy"))
    month = NOW.astimezone().strftime("%Y-%m")
    assert [p["track"] for p in client.get("/listening/recent").json()] == ["Blinding Lights", "Starboy"]
    activity = client.get("/listening/activity").json()
    assert sum(activity["minutes_per_day"].values()) == 6.0 and sum(activity["minutes_per_week"].values()) == 6.0
    assert client.get("/listening/top-artists", params={"month": month}).json()[0] == {"artist": "The Weeknd", "plays": 2}
    assert len(client.get("/listening/top-tracks", params={"month": month}).json()) == 2
    assert client.get("/listening/top-artists", params={"month": "geen-maand"}).status_code == 422


# ---- Webpagina ----

def test_pages_render(client):
    client.post("/listening/plays", json=body())
    for page in ["dashboard", "history", "progress"]:
        response = client.get(f"/ui/listening/{page}")
        assert response.status_code == 200, page
        assert "Blinding Lights" in response.text or page == "progress"


def test_dashboard_shows_minutes_this_week_and_top_artists(client):
    for i, artist in enumerate(["A", "A", "B", "C", "D", "E", "F"]):
        client.post("/listening/plays", json=body(NOW - timedelta(minutes=4 * i), artists=[artist],
                                                  track_id=f"spotify:track:{i}"))
    page = client.get("/ui/listening/dashboard").text
    assert '<div class="stat-value">21' in page and "Minutes this week" in page  # 7 plays × 3 min
    top = page.split("Top artists", 1)[1]
    assert top.index(">A<") < top.index(">B<") and ">F<" not in top.split("</section>", 1)[0]


def test_empty_dashboard_points_to_settings(client):
    page = client.get("/ui/listening/dashboard").text
    assert 'href="#settings"' in page


def test_listening_is_a_tracker_everywhere(client):
    client.post("/listening/plays", json=body())
    home = client.get("/ui/home").text
    assert "Listening" in home and 'href="#listening"' in home and "min this week" in home
    assert "Listening" in client.get("/ui/settings").text
    assert 'href="#listening"' in client.get("/").text
    assert "listening:" in client.get("/static/trackly.js").text


def test_restore_refreshes_listening_too(client):
    client.post("/ui/backups")
    name = client.get("/ui/backups").text.split("/ui/backups/")[1].split("/")[0]
    assert "listening-changed" in client.post(f"/ui/backups/{name}/restore").headers["HX-Trigger"]


def test_home_note_without_artists_has_no_loose_dot(client):
    client.post("/listening/plays", json=body(artists=[]))
    home = client.get("/ui/home").text
    assert "Last played: Blinding Lights</p>" in home and "Blinding Lights ·" not in home
