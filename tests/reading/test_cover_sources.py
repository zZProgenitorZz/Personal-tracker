"""Elke coverbron apart: wat vraagt hij, en wat haalt hij uit het antwoord?
Alle API's zijn nep (httpx.MockTransport)."""
import json

import httpx
import pytest

from app.covers import CoverError
from app.reading.cover_sources import anilist, google_books, mangaupdates, open_library


def client_answering(answer, seen=None):
    def handle(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    return httpx.Client(transport=httpx.MockTransport(handle))


# ---- AniList ----

def test_anilist_reads_all_titles_and_largest_cover():
    seen = []
    client = client_answering({"data": {"Page": {"media": [
        {"title": {"romaji": "Ore dake Level Up na Ken", "english": "Solo Leveling", "native": "나 혼자만 레벨업"},
         "synonyms": ["Only I Level Up"], "coverImage": {"extraLarge": "https://a/xl.jpg", "large": "https://a/l.jpg"}},
        {"title": {"romaji": "Zonder cover"}, "synonyms": [], "coverImage": {}},
    ]}}}, seen)

    [result] = anilist(client, "Solo Leveling")

    assert result.source == "AniList"
    assert result.image_url == "https://a/xl.jpg"
    assert set(result.titles) == {"Solo Leveling", "Ore dake Level Up na Ken", "나 혼자만 레벨업", "Only I Level Up"}
    body = json.loads(seen[0].content)
    assert body["variables"]["search"] == "Solo Leveling" and "type: MANGA" in body["query"]


# ---- MangaUpdates ----

def test_mangaupdates_reads_novels_too():
    seen = []
    client = client_answering({"results": [
        {"hit_title": "Shadow Slave", "record": {"title": "Shadow Slave (Novel)", "type": "Novel",
                                                 "image": {"url": {"original": "https://mu/o.jpg", "thumb": "https://mu/t.jpg"}}}},
        {"hit_title": "Zonder plaatje", "record": {"title": "Zonder plaatje", "type": "Manhwa", "image": {"url": {}}}},
    ]}, seen)

    [result] = mangaupdates(client, "Shadow Slave")

    assert result.source == "MangaUpdates"
    assert result.image_url == "https://mu/o.jpg"
    assert set(result.titles) == {"Shadow Slave (Novel)", "Shadow Slave"}
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content)["search"] == "Shadow Slave"
    assert "type" not in json.loads(seen[0].content)  # geen typefilter: novels én manhwa


# ---- Google Books ----

def test_google_books_asks_for_largest_version_over_https(monkeypatch):
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    seen = []
    client = client_answering({"items": [
        {"volumeInfo": {"title": "Shadow Slave", "subtitle": "Book 1", "imageLinks": {
            "smallThumbnail": "http://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=5&edge=curl",
            "thumbnail": "http://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=1&edge=curl",
        }}},
        {"volumeInfo": {"title": "Zonder plaatje"}},
    ]}, seen)

    [result] = google_books(client, "Shadow Slave")

    assert result.source == "Google Books"
    assert result.image_url == "https://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=0"
    assert result.check_size  # Google-thumbnails kunnen te klein zijn
    assert set(result.titles) == {"Shadow Slave", "Shadow Slave: Book 1"}
    assert seen[0].url.params["q"] == "intitle:Shadow Slave"
    assert "key" not in seen[0].url.params


def test_google_books_prefers_extra_large_link():
    client = client_answering({"items": [{"volumeInfo": {"title": "A", "imageLinks": {
        "thumbnail": "https://g/t?zoom=1", "extraLarge": "https://g/xl?zoom=6"}}}]})
    assert google_books(client, "A")[0].image_url == "https://g/xl?zoom=6"


def test_google_books_uses_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "geheim")
    seen = []
    google_books(client_answering({"totalItems": 0}, seen), "Shadow Slave")
    assert seen[0].url.params["key"] == "geheim"


def test_google_books_error_never_shows_the_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "geheim")
    with pytest.raises(CoverError) as error:
        google_books(client_answering(httpx.Response(403)), "Shadow Slave")
    assert "geheim" not in str(error.value)


# ---- Open Library ----

def test_open_library_builds_large_cover_url():
    seen = []
    client = client_answering({"docs": [
        {"title": "Shadow Slave", "alternative_title": ["Shadow Slave Vol. 1"], "cover_i": 15100918},
        {"title": "Zonder cover"},
    ]}, seen)

    [result] = open_library(client, "Shadow Slave")

    assert result.source == "Open Library"
    assert result.image_url == "https://covers.openlibrary.org/b/id/15100918-L.jpg"
    assert set(result.titles) == {"Shadow Slave", "Shadow Slave Vol. 1"}
    assert seen[0].url.params["title"] == "Shadow Slave"


# ---- Fouten zijn voor elke bron hetzelfde ----

SOURCES = [(anilist, "AniList"), (mangaupdates, "MangaUpdates"),
           (google_books, "Google Books"), (open_library, "Open Library")]


@pytest.mark.parametrize("source, name", SOURCES)
def test_rate_limit_names_the_source(source, name):
    with pytest.raises(CoverError, match=f"{name} is busy"):
        source(client_answering(httpx.Response(429)), "x")


@pytest.mark.parametrize("source, name", SOURCES)
def test_timeout_names_the_source(source, name):
    with pytest.raises(CoverError, match=f"{name} took too long"):
        source(client_answering(httpx.ReadTimeout("traag")), "x")


@pytest.mark.parametrize("source, name", SOURCES)
def test_network_error_names_the_source(source, name):
    with pytest.raises(CoverError, match=f"reach {name}"):
        source(client_answering(httpx.ConnectError("offline")), "x")


@pytest.mark.parametrize("source, name", SOURCES)
def test_unreadable_answer_names_the_source(source, name):
    with pytest.raises(CoverError, match=name):
        source(client_answering(httpx.Response(200, text="<html>")), "x")
