"""Coverbronnen voor Watching, met nep-API's (geen netwerk)."""
import json

import httpx
import pytest

from app.covers import CoverError
from app.watching.cover_sources import TMDB_KEY_ENV, anilist_anime, tmdb, tvmaze


def client_answering(answer, seen=None):
    def handle(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_anilist_anime():
    seen = []
    client = client_answering({"data": {"Page": {"media": [{
        "title": {"english": "Frieren: Beyond Journey's End", "romaji": "Sousou no Frieren", "native": None},
        "synonyms": [], "coverImage": {"extraLarge": "https://a/f.jpg"},
        "genres": ["Adventure", "Drama", "Fantasy"], "tags": [{"name": "Isekai", "rank": 10}],
    }]}}}, seen)
    [result] = anilist_anime(client, "Frieren")
    assert (result.source, result.image_url) == ("AniList", "https://a/f.jpg")
    assert result.genres == ("Adventure", "Drama", "Fantasy")
    assert "type: ANIME" in json.loads(seen[0].content)["query"]


def test_tvmaze_series():
    seen = []
    client = client_answering([
        {"score": 0.9, "show": {"name": "Breaking Bad", "genres": ["Drama", "Crime", "Thriller"],
                                "image": {"medium": "https://t/m.jpg", "original": "https://t/o.jpg"}}},
        {"score": 0.5, "show": {"name": "Zonder plaatje", "genres": [], "image": None}},
    ], seen)
    [result] = tvmaze(client, "Breaking Bad")
    assert (result.source, result.image_url, result.titles) == ("TVmaze", "https://t/o.jpg", ("Breaking Bad",))
    assert result.genres == ("Crime", "Drama", "Thriller")
    assert seen[0].url.params["q"] == "Breaking Bad"


def test_tmdb_is_skipped_without_key(monkeypatch):
    monkeypatch.delenv(TMDB_KEY_ENV, raising=False)
    seen = []
    assert tmdb(client_answering({"results": []}, seen), "Dune") == []
    assert seen == []  # geen verzoek zonder sleutel


@pytest.mark.parametrize("key, in_params, in_header", [
    ("abc123", True, False),                                  # korte "API Key"
    ("eyJhbGciOi.eyJhdWQiOi.signature", False, True),         # lange "Read Access Token"
])
def test_tmdb_accepts_both_kinds_of_key(monkeypatch, key, in_params, in_header):
    monkeypatch.setenv(TMDB_KEY_ENV, key)
    seen = []
    tmdb(client_answering({"results": []}, seen), "Dune")
    assert ("api_key" in seen[0].url.params) is in_params
    assert (seen[0].headers.get("authorization") == f"Bearer {key}") is in_header


def test_tmdb_movies_and_series(monkeypatch):
    monkeypatch.setenv(TMDB_KEY_ENV, "abc123")
    client = client_answering({"results": [
        {"media_type": "movie", "title": "Dune: Part Two", "original_title": "Dune: Part Two",
         "poster_path": "/p.jpg", "genre_ids": [878, 12]},
        {"media_type": "tv", "name": "Dune: Prophecy", "original_name": "Dune: Prophecy",
         "poster_path": "/q.jpg", "genre_ids": [10765, 18]},
        {"media_type": "person", "name": "Zendaya", "profile_path": "/z.jpg"},
        {"media_type": "movie", "title": "Zonder poster", "poster_path": None, "genre_ids": []},
    ]})
    movie, series = tmdb(client, "Dune")
    assert movie.image_url == "https://image.tmdb.org/t/p/w780/p.jpg"
    assert movie.genres == ("Adventure", "Sci-Fi")
    assert series.titles == ("Dune: Prophecy",) and series.genres == ("Drama", "Fantasy", "Sci-Fi")


def test_tmdb_error_never_shows_the_key(monkeypatch):
    monkeypatch.setenv(TMDB_KEY_ENV, "geheim")
    with pytest.raises(CoverError) as error:
        tmdb(client_answering(httpx.Response(401)), "Dune")
    assert "geheim" not in str(error.value) and "TMDB" in str(error.value)


@pytest.mark.parametrize("source, name", [(anilist_anime, "AniList"), (tvmaze, "TVmaze")])
def test_errors_name_the_source(source, name):
    with pytest.raises(CoverError, match=f"{name} is busy"):
        source(client_answering(httpx.Response(429)), "x")
