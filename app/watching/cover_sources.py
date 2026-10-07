"""Coverbronnen voor Watching. Zelfde vorm als bij Reading:

    bron(client, zoekterm) -> list[SourceResult]

Een bron toevoegen of weghalen: schrijf een functie met @cover_source("Naam")
erboven en zet hem in SOURCES (of haal hem eruit).
"""
import os

import httpx

from ..cover_search import SourceResult, ask, cover_source, unique_titles
from .genres import WATCH_GENRES

ANILIST_MIN_TAG_RANK = 60


# ---- AniList: anime ----

ANILIST_URL = "https://graphql.anilist.co"
ANILIST_QUERY = """
query ($search: String) {
  Page(perPage: 25) {
    media(search: $search, type: ANIME) {
      title { romaji english native }
      synonyms
      coverImage { extraLarge large }
      genres
      tags { name rank }
    }
  }
}
"""


@cover_source("AniList")
def anilist_anime(client: httpx.Client, term: str) -> list[SourceResult]:
    data = ask(client, "AniList", "POST", ANILIST_URL,
               json={"query": ANILIST_QUERY, "variables": {"search": term}})
    results = []
    for media in ((data or {}).get("data") or {}).get("Page", {}).get("media") or []:
        names = media.get("title") or {}
        images = media.get("coverImage") or {}
        image = images.get("extraLarge") or images.get("large")
        titles = unique_titles(names.get("english"), names.get("romaji"), names.get("native"),
                               *(media.get("synonyms") or []))
        tags = [t.get("name") for t in media.get("tags") or [] if (t.get("rank") or 0) >= ANILIST_MIN_TAG_RANK]
        genres = tuple(WATCH_GENRES.from_source([*(media.get("genres") or []), *tags]))
        if image and titles:
            results.append(SourceResult(titles, image, "AniList", genres=genres))
    return results


# ---- TVmaze: series (geen sleutel nodig) ----

TVMAZE_URL = "https://api.tvmaze.com/search/shows"


@cover_source("TVmaze")
def tvmaze(client: httpx.Client, term: str) -> list[SourceResult]:
    data = ask(client, "TVmaze", "GET", TVMAZE_URL, params={"q": term})
    results = []
    for hit in data if isinstance(data, list) else []:
        show = (hit or {}).get("show") or {}
        images = show.get("image") or {}
        image = images.get("original") or images.get("medium")
        titles = unique_titles(show.get("name"))
        if image and titles:
            genres = tuple(WATCH_GENRES.from_source(show.get("genres") or []))
            results.append(SourceResult(titles, image, "TVmaze", genres=genres))
    return results


# ---- TMDB: films en series (optioneel, met gratis sleutel) ----

TMDB_URL = "https://api.themoviedb.org/3/search/multi"
TMDB_POSTER = "https://image.tmdb.org/t/p/w780{}"
TMDB_KEY_ENV = "TMDB_API_KEY"  # een "API Key" of een "API Read Access Token" van themoviedb.org
TMDB_GENRES = {  # TMDB geeft genre-ids; dit zijn hun vaste namen (films en series samen)
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime", 99: "Documentary",
    18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Science Fiction", 53: "Thriller", 10752: "War",
    10759: "Action & Adventure", 10762: "Kids", 10765: "Sci-Fi & Fantasy", 10768: "War & Politics",
}


@cover_source("TMDB")
def tmdb(client: httpx.Client, term: str) -> list[SourceResult]:
    key = os.environ.get(TMDB_KEY_ENV, "").strip()
    if not key:
        return []  # zonder sleutel doet TMDB niet mee
    params, headers = {"query": term, "include_adult": "false"}, {}
    if "." in key:  # een Read Access Token (JWT) gaat in de header
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    data = ask(client, "TMDB", "GET", TMDB_URL, params=params, headers=headers)
    results = []
    for item in (data or {}).get("results") or []:
        if item.get("media_type") not in ("movie", "tv") or not item.get("poster_path"):
            continue
        titles = unique_titles(item.get("title") or item.get("name"),
                               item.get("original_title") or item.get("original_name"))
        genres = tuple(WATCH_GENRES.from_source(TMDB_GENRES.get(i, "") for i in item.get("genre_ids") or []))
        if titles:
            results.append(SourceResult(titles, TMDB_POSTER.format(item["poster_path"]), "TMDB", genres=genres))
    return results


# Volgorde = volgorde bij gelijke score.
SOURCES = [anilist_anime, tvmaze, tmdb]
