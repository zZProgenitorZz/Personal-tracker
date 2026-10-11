"""Sources for covers. Each source is a function with the same shape:

    source(client, search_term) -> list[SourceResult]

A source only searches and reads the answer; comparing, sorting and
combining happens in app/cover_search.py. On an error a source raises a
CoverError with its own name in it. To add or remove a source: write
a function like the ones below, with @cover_source("Name") above it, and put it
in SOURCES (or take it out).
"""
import os
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from ..cover_search import SOURCE_TIMEOUT, SourceBusy, SourceResult, ask, cover_source, unique_titles
from ..covers import CoverError
from .genres import from_source

ANILIST_MIN_TAG_RANK = 60  # only tags AniList is sure about


# ---- AniList: manga, manhwa and light novels ----

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


# ---- MangaUpdates: web novels too (type "Novel") ----

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


# ---- MyAnimeList through Jikan: (light) novels, Chinese and Korean web novels too ----

JIKAN_URL = "https://api.jikan.moe/v4/manga"
JIKAN_TYPES = ("novel", "lightnovel")
JIKAN_MIN_INTERVAL = 0.4  # seconds between requests; Jikan allows at most 3 per second
JIKAN_CONNECT_TIMEOUT = 3.0  # if Jikan doesn't pick up, give up quickly
JIKAN_COOLDOWN = 5 * 60   # after a network error skip Jikan this long, so searching doesn't keep waiting
_jikan_lock = threading.Lock()
_jikan_last = [0.0]
_jikan_down_until = [0.0]


def _jikan_pause() -> None:
    """Waits until JIKAN_MIN_INTERVAL has passed since the previous Jikan request."""
    with _jikan_lock:
        # Never longer than JIKAN_MIN_INTERVAL, even if the clock jumps back.
        wait = min(JIKAN_MIN_INTERVAL, _jikan_last[0] + JIKAN_MIN_INTERVAL - time.monotonic())
        if wait > 0:
            time.sleep(wait)
        _jikan_last[0] = time.monotonic()


@cover_source("MyAnimeList")
def myanimelist(client: httpx.Client, term: str) -> list[SourceResult]:
    """Searches novels and light novels separately. On a 429 this source stops: what was already
    found counts, and without results only this source drops out (not the whole search)."""
    if _jikan_down_until[0] > time.monotonic():
        raise CoverError("MyAnimeList is unreachable; Progen tries it again in a few minutes.")
    found: dict[int, SourceResult] = {}
    for kind in JIKAN_TYPES:
        _jikan_pause()
        try:
            data = ask(client, "MyAnimeList", "GET", JIKAN_URL, params={"q": term, "type": kind, "limit": 25},
                       timeout=httpx.Timeout(SOURCE_TIMEOUT, connect=JIKAN_CONNECT_TIMEOUT))
        except SourceBusy:
            if found:
                break
            raise
        except CoverError as exc:
            if isinstance(exc.__cause__, httpx.HTTPError):  # unreachable or too slow
                _jikan_down_until[0] = time.monotonic() + JIKAN_COOLDOWN
            raise
        for item in (data or {}).get("data") or []:
            images = item.get("images") or {}
            image = ((images.get("jpg") or {}).get("large_image_url")
                     or (images.get("webp") or {}).get("large_image_url")
                     or (images.get("jpg") or {}).get("image_url"))
            titles = unique_titles(
                item.get("title_english"), item.get("title"), item.get("title_japanese"),
                *(item.get("title_synonyms") or []),
                *((t or {}).get("title") for t in item.get("titles") or []),
            )
            names = [(g or {}).get("name") for key in ("genres", "themes") for g in item.get(key) or []]
            if image and titles and item.get("mal_id") not in found:
                found[item.get("mal_id")] = SourceResult(titles, image, "MyAnimeList", genres=tuple(from_source(names)))
    return list(found.values())


# ---- Google Books: published books, often small thumbnails ----

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
GOOGLE_BOOKS_KEY_ENV = "GOOGLE_BOOKS_API_KEY"  # optional; without a key a tight shared quota applies
_GOOGLE_SIZES = ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail")


def _largest_google_image(links: dict) -> str | None:
    for size in _GOOGLE_SIZES:
        if links.get(size):
            url = links[size]
            break
    else:
        return None
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "edge"]  # no curled corner
    if size in ("thumbnail", "smallThumbnail"):
        query = [(k, "0" if k == "zoom" else v) for k, v in query]  # zoom=0: largest version
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


# ---- Open Library: books, also many web novels in print ----

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


# Order = the order on a tie.
SOURCES = [anilist, mangaupdates, myanimelist, open_library, google_books]
