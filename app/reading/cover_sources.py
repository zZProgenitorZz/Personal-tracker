"""Bronnen voor covers. Elke bron is een functie met dezelfde vorm:

    bron(client, zoekterm) -> list[SourceResult]

Een bron zoekt alleen en leest het antwoord uit; vergelijken, sorteren en
combineren gebeurt in app/cover_search.py. Bij een fout gooit een bron een
CoverError met zijn eigen naam erin. Een bron toevoegen of weghalen: schrijf
een functie zoals hieronder, met @cover_source("Naam") erboven, en zet hem
in SOURCES (of haal hem eruit).
"""
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from ..cover_search import SourceResult, ask, cover_source, unique_titles
from .genres import from_source

ANILIST_MIN_TAG_RANK = 60  # alleen tags waar AniList zeker van is


# ---- AniList: manga, manhwa en light novels ----

ANILIST_URL = "https://graphql.anilist.co"
ANILIST_QUERY = """
query ($search: String) {
  Page(perPage: 25) {
    media(search: $search, type: MANGA) {
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
def anilist(client: httpx.Client, term: str) -> list[SourceResult]:
    data = ask(client, "AniList", "POST", ANILIST_URL,
                json={"query": ANILIST_QUERY, "variables": {"search": term}})
    results = []
    for media in ((data or {}).get("data") or {}).get("Page", {}).get("media") or []:
        names = media.get("title") or {}
        image = (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large")
        titles = unique_titles(names.get("english"), names.get("romaji"), names.get("native"), *(media.get("synonyms") or []))
        tags = [t.get("name") for t in media.get("tags") or [] if (t.get("rank") or 0) >= ANILIST_MIN_TAG_RANK]
        genres = tuple(from_source([*(media.get("genres") or []), *tags]))
        if image and titles:
            results.append(SourceResult(titles, image, "AniList", genres=genres))
    return results


# ---- MangaUpdates: ook webnovels (type "Novel") ----

MANGAUPDATES_URL = "https://api.mangaupdates.com/v1/series/search"


@cover_source("MangaUpdates")
def mangaupdates(client: httpx.Client, term: str) -> list[SourceResult]:
    data = ask(client, "MangaUpdates", "POST", MANGAUPDATES_URL, json={"search": term, "perpage": 25})
    results = []
    for hit in (data or {}).get("results") or []:
        record = hit.get("record") or {}
        image = (((record.get("image") or {}).get("url")) or {}).get("original")
        titles = unique_titles(record.get("title"), hit.get("hit_title"))
        genres = tuple(from_source(g.get("genre") for g in record.get("genres") or [] if isinstance(g, dict)))
        if image and titles:
            results.append(SourceResult(titles, image, "MangaUpdates", genres=genres))
    return results


# ---- Google Books: gepubliceerde boeken, vaak kleine thumbnails ----

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
GOOGLE_BOOKS_KEY_ENV = "GOOGLE_BOOKS_API_KEY"  # optioneel; zonder sleutel geldt een krap gedeeld quotum
_GOOGLE_SIZES = ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail")


def _largest_google_image(links: dict) -> str | None:
    for size in _GOOGLE_SIZES:
        if links.get(size):
            url = links[size]
            break
    else:
        return None
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "edge"]  # geen omgekrulde hoek
    if size in ("thumbnail", "smallThumbnail"):
        query = [(k, "0" if k == "zoom" else v) for k, v in query]  # zoom=0: grootste versie
    return urlunsplit(("https", parts.netloc, parts.path, urlencode(query), ""))


@cover_source("Google Books")
def google_books(client: httpx.Client, term: str) -> list[SourceResult]:
    params = {"q": f"intitle:{term}", "maxResults": 20, "printType": "books"}
    if key := os.environ.get(GOOGLE_BOOKS_KEY_ENV):
        params["key"] = key
    data = ask(client, "Google Books", "GET", GOOGLE_BOOKS_URL, params=params)
    results = []
    for item in (data or {}).get("items") or []:
        info = item.get("volumeInfo") or {}
        image = _largest_google_image(info.get("imageLinks") or {})
        title, subtitle = info.get("title"), info.get("subtitle")
        titles = unique_titles(title, f"{title}: {subtitle}" if title and subtitle else None)
        genres = tuple(from_source(info.get("categories") or []))
        if image and titles:
            results.append(SourceResult(titles, image, "Google Books", check_size=True, genres=genres))
    return results


# ---- Open Library: boeken, ook veel webnovels in print ----

OPEN_LIBRARY_URL = "https://openlibrary.org/search.json"
OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/id/{}-L.jpg"


@cover_source("Open Library")
def open_library(client: httpx.Client, term: str) -> list[SourceResult]:
    data = ask(client, "Open Library", "GET", OPEN_LIBRARY_URL, params={
        "title": term, "limit": 20, "fields": "title,subtitle,alternative_title,cover_i,subject",
    })
    results = []
    for doc in (data or {}).get("docs") or []:
        cover = doc.get("cover_i")
        alternative = doc.get("alternative_title") or []
        titles = unique_titles(doc.get("title"), *(alternative if isinstance(alternative, list) else [alternative]))
        genres = tuple(from_source(doc.get("subject") or []))
        if isinstance(cover, int) and cover > 0 and titles:
            results.append(SourceResult(titles, OPEN_LIBRARY_COVER.format(cover), "Open Library", genres=genres))
    return results


# Volgorde = volgorde bij gelijke score.
SOURCES = [anilist, mangaupdates, open_library, google_books]
