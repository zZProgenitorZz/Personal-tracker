"""Covers van begin tot eind: zoeken, opslaan met en zonder cover, en tonen.
AniList en downloads zijn nep; er gaat niets over het netwerk."""
import json
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app

SOLO_1 = "https://img.anili.st/solo-1.jpg"
SOLO_2 = "https://img.anili.st/solo-2.jpg"


def png() -> bytes:
    out = BytesIO()
    Image.new("RGB", (200, 300), "purple").save(out, "PNG")
    return out.getvalue()


def fake_internet(request: httpx.Request) -> httpx.Response:
    if request.url.host == "graphql.anilist.co":
        term = json.loads(request.content)["variables"]["search"]
        found = []
        if "solo" in term.lower():
            found = [
                {"title": {"romaji": "Ore dake Level Up na Ken", "english": "Solo Leveling", "native": None},
                 "synonyms": [], "coverImage": {"extraLarge": SOLO_1}},
                {"title": {"romaji": "Na Honjaman Level Up", "english": None, "native": None},
                 "synonyms": ["Solo Levelling"], "coverImage": {"extraLarge": SOLO_2}},
            ]
        return httpx.Response(200, json={"data": {"Page": {"media": found}}})
    if request.url.path.endswith(".jpg") or request.url.path.endswith(".png"):
        return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})
    return httpx.Response(200, text="<html>geen plaatje</html>", headers={"Content-Type": "text/html"})


@pytest.fixture
def covers(tmp_path):
    return tmp_path / "covers"


@pytest.fixture
def client(covers):
    http = httpx.Client(transport=httpx.MockTransport(fake_internet))
    return TestClient(create_app(":memory:", covers_dir=covers, http_client=http))


def add(client, title="Solo Leveling", files=None, **fields):
    data = {"title": title, "kind": "manhwa", "source": "asura", "start_chapter": "1", **fields}
    return client.post("/ui/series", data=data, files=files)


def library(client):
    return client.get("/reading/library").json()


# ---- Zoeken (hulp-endpoint, geen command) ----

def test_search_shows_first_match(client):
    response = client.get("/ui/covers/search", params={"title": "solo leveling"})
    assert SOLO_1 in response.text
    assert "1 of 2" in response.text
    assert 'alt="Cover of Solo Leveling"' in response.text


def test_search_cycles_through_results(client):
    second = client.get("/ui/covers/search", params={"title": "Solo Leveling", "index": 1})
    wrapped = client.get("/ui/covers/search", params={"title": "Solo Leveling", "index": 2})
    assert SOLO_2 in second.text and "2 of 2" in second.text
    assert SOLO_1 in wrapped.text and "1 of 2" in wrapped.text


def test_search_without_results_says_so(client):
    response = client.get("/ui/covers/search", params={"title": "Iets Onbekends"})
    assert "No covers found" in response.text
    assert 'name="cover_url"' not in response.text


def test_search_needs_a_title(client):
    assert "Type a title" in client.get("/ui/covers/search", params={"title": " "}).text


def test_search_does_not_store_anything(client):
    client.get("/ui/covers/search", params={"title": "Solo Leveling"})
    assert library(client) == []


# ---- Opslaan ----

def test_save_without_cover(client, covers):
    add(client)
    assert library(client)[0]["cover"] is None
    assert list(covers.iterdir()) == []


def test_save_with_found_cover(client, covers):
    response = add(client, cover_url=SOLO_1)
    assert "toast-error" not in response.text

    [entry] = library(client)
    assert (covers / entry["cover"]).exists()
    assert client.get(f"/covers/{entry['cover']}").headers["content-type"] == "image/webp"
    assert f'/covers/{entry["cover"]}' in client.get("/ui/library/grid").text


def test_save_with_pasted_url(client):
    add(client, cover_link="https://example.com/mine.png")
    assert library(client)[0]["cover"] is not None


def test_save_with_upload(client, covers):
    add(client, files={"cover_file": ("mijn cover.png", png(), "image/png")})
    [entry] = library(client)
    assert entry["cover"].endswith(".webp") and "mijn" not in entry["cover"]


def test_upload_wins_over_url_and_search(client, covers):
    add(client, cover_url=SOLO_1, cover_link="https://example.com/page",
        files={"cover_file": ("c.png", png(), "image/png")})
    assert library(client)[0]["cover"] is not None
    assert len(list(covers.iterdir())) == 1


def test_broken_cover_does_not_block_saving(client):
    response = add(client, cover_link="https://example.com/page")
    assert "toast-error" in response.text
    assert "couldn&#39;t be used" in response.text
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert library(client)[0]["cover"] is None


def test_invalid_upload_does_not_block_saving(client):
    add(client, files={"cover_file": ("x.png", b"geen plaatje", "image/png")})
    assert library(client)[0]["cover"] is None


def test_rejected_series_leaves_no_cover_file(client, covers):
    add(client, cover_url=SOLO_1)
    response = add(client, title="solo leveling", cover_url=SOLO_2)
    assert "staat al in je bibliotheek" in response.text
    assert len(list(covers.iterdir())) == 1


def test_json_api_accepts_cover_url(client):
    response = client.post("/reading/series", json={
        "title": "Solo Leveling", "kind": "manhwa", "source": "asura", "cover_url": SOLO_1,
    })
    assert response.status_code == 201
    assert response.json()["cover"].endswith(".webp")


def test_json_api_saves_series_when_cover_fails(client):
    response = client.post("/reading/series", json={
        "title": "Solo Leveling", "kind": "manhwa", "source": "asura", "cover_url": "ftp://nope",
    })
    assert response.status_code == 201
    assert response.json()["cover"] is None
    assert "http" in response.json()["cover_error"]
