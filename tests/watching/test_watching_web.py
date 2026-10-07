"""Watching via de JSON-API en de webpagina, plus het startscherm. Geen netwerk."""
import json
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app

FRIEREN = "https://img.anili.st/frieren.jpg"


def png() -> bytes:
    out = BytesIO()
    Image.new("RGB", (200, 300), "teal").save(out, "PNG")
    return out.getvalue()


def fake_internet(request: httpx.Request) -> httpx.Response:
    if request.url.host == "graphql.anilist.co":
        body = json.loads(request.content)
        found = []
        if "type: ANIME" in body["query"] and "frieren" in body["variables"]["search"].lower():
            found = [{"title": {"english": "Frieren", "romaji": "Sousou no Frieren", "native": None},
                      "synonyms": [], "coverImage": {"extraLarge": FRIEREN}, "genres": ["Adventure", "Fantasy"]}]
        return httpx.Response(200, json={"data": {"Page": {"media": found}}})
    if request.url.host == "api.tvmaze.com":
        return httpx.Response(200, json=[])
    if request.url.path.endswith(".jpg"):
        return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})
    return httpx.Response(404)


@pytest.fixture
def client(tmp_path):
    http = httpx.Client(transport=httpx.MockTransport(fake_internet))
    return TestClient(create_app(":memory:", covers_dir=tmp_path / "covers", http_client=http))


def add(client, title="Frieren", kind="anime", **fields):
    return client.post("/ui/watching/shows", data={"title": title, "kind": kind, **fields})


def watchlist(client):
    return client.get("/watching/list").json()


# ---- JSON-API ----

def test_api_add_change_genres_remove(client):
    response = client.post("/watching/shows", json={"title": "Dune", "kind": "movie", "status": "completed",
                                                     "genres": ["Sci-Fi", "Adventure"]})
    assert response.status_code == 201
    show = response.json()
    assert (show["status"], show["genres"]) == ("completed", ["Adventure", "Sci-Fi"])

    sid = show["show_id"]
    assert client.post(f"/watching/shows/{sid}/status", json={"status": "watching"}).json()["status"] == "watching"
    assert client.post(f"/watching/shows/{sid}/genres", json={"genres": ["War"]}).json()["genres"] == ["War"]
    assert [s["title"] for s in client.get("/watching/current").json()] == ["Dune"]
    assert client.delete(f"/watching/shows/{sid}").status_code == 204
    assert watchlist(client) == []


def test_api_rules_give_clear_errors(client):
    client.post("/watching/shows", json={"title": "Dune", "kind": "movie"})
    assert client.post("/watching/shows", json={"title": "dune", "kind": "movie"}).status_code == 400
    assert client.post("/watching/shows", json={"title": "X", "kind": "podcast"}).status_code == 422
    assert client.post("/watching/shows/bestaat-niet/status", json={"status": "completed"}).status_code == 404


def test_api_filter_by_status(client):
    client.post("/watching/shows", json={"title": "A", "kind": "series"})
    client.post("/watching/shows", json={"title": "B", "kind": "series", "status": "completed"})
    assert [s["title"] for s in client.get("/watching/list", params={"status": "completed"}).json()] == ["B"]


# ---- Webpagina ----

def test_pages_render(client):
    add(client)
    for page in ["dashboard", "library", "library/grid", "progress", "add-form"]:
        response = client.get(f"/ui/watching/{page}")
        assert response.status_code == 200, page
    assert "Frieren" in client.get("/ui/watching/dashboard").text


def test_add_with_status_genres_and_found_cover(client, tmp_path):
    response = add(client, status="completed", genres=["Fantasy"], cover_url=FRIEREN)
    assert response.headers["HX-Trigger"] == "watching-changed"
    [show] = watchlist(client)
    assert (show["status"], show["genres"]) == ("completed", ["Fantasy"])
    assert (tmp_path / "covers" / show["cover"]).exists()


def test_broken_cover_does_not_block_adding(client):
    response = add(client, cover_link="https://example.com/not-an-image")
    assert "toast-error" in response.text and watchlist(client)[0]["cover"] is None


def test_status_genres_and_remove_via_web(client):
    add(client)
    sid = watchlist(client)[0]["show_id"]
    assert client.post(f"/ui/watching/shows/{sid}/status", data={"status": "on_hold"}).headers["HX-Trigger"] == "watching-changed"
    assert 'value="Fantasy"' in client.get(f"/ui/watching/shows/{sid}/genres").text
    client.post(f"/ui/watching/shows/{sid}/genres", data={"genres": ["Drama"]})
    assert watchlist(client)[0]["genres"] == ["Drama"] and watchlist(client)[0]["status"] == "on_hold"
    client.delete(f"/ui/watching/shows/{sid}")
    assert watchlist(client) == []


def test_library_filters(client):
    add(client, "Frieren", "anime", genres=["Fantasy"])
    add(client, "Dune", "movie", status="completed", genres=["Sci-Fi"])
    grid = lambda **p: client.get("/ui/watching/library/grid", params=p).text  # noqa: E731
    assert "Dune" in grid(kind="movie") and "Frieren" not in grid(kind="movie")
    assert "Frieren" in grid(genre="Fantasy") and "Dune" not in grid(genre="Fantasy")
    assert "Dune" in grid(status="completed") and "Frieren" not in grid(status="completed")
    assert "Frieren" in grid(q="frier")


def test_cover_search_uses_watching_sources(client):
    response = client.get("/ui/watching/covers/search", params={"title": "Frieren"})
    assert FRIEREN in response.text and 'data-genres="Adventure,Fantasy"' in response.text


def test_add_forms_list_their_own_genres(client):
    from app.reading.genres import GENRES
    from app.watching.genres import WATCH_GENRES
    reading_form = client.get("/ui/reading/add-form").text
    watching_form = client.get("/ui/watching/add-form").text
    assert 'value="Cultivation"' in reading_form and 'value="Cultivation"' not in watching_form
    assert all(f'value="{g}"' in watching_form for g in WATCH_GENRES.names)
    assert all(f'value="{g}"' in reading_form for g in GENRES)


# ---- Startscherm en opslag op één plek ----

def test_home_shows_every_tracker(client):
    add(client)
    client.post("/ui/reading/series", data={"title": "Shadow Slave", "kind": "novel", "source": "x", "start_chapter": "1"})
    home = client.get("/ui/home").text
    assert "Reading" in home and "Watching" in home
    assert 'href="#reading"' in home and 'href="#watching"' in home


def test_one_backup_contains_every_tracker(client, tmp_path):
    add(client)
    client.post("/ui/reading/series", data={"title": "Shadow Slave", "kind": "novel", "source": "x", "start_chapter": "1"})
    client.post("/ui/backups")
    settings = client.get("/ui/settings").text
    assert "Watching" in settings
    # De back-up zit in de tijdelijke map van de test-app; via de lijst vinden we hem.
    name = client.get("/ui/backups").text.split("/ui/backups/")[1].split("/")[0]
    restore = client.post(f"/ui/backups/{name}/restore")
    assert "watching-changed" in restore.headers["HX-Trigger"] and "reading-changed" in restore.headers["HX-Trigger"]
    assert [s["title"] for s in watchlist(client)] == ["Frieren"]
