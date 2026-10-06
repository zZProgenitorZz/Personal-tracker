"""Covers zoeken bij AniList voor novels en manhwa.

Een hulpmiddel bij het invullen van het formulier: geen command en geen
event. Pas als je de serie opslaat, wordt de gekozen cover gedownload.
"""
import re
import time
import unicodedata
from dataclasses import dataclass

import httpx
from rapidfuzz import fuzz

from ..covers import CoverError

# Hoe streng de titels moeten overeenkomen (0–100). Lager vindt meer, maar
# ook vaker een verkeerde serie. 85 laat typfouten en een ontbrekend lidwoord
# door, maar geen vervolgdelen als "Solo Leveling: Ragnarok".
COVER_MATCH_THRESHOLD = 85
# AniList zoekt letterlijk: één typfout en hij vindt niets. Daarom zoeken we als
# laatste stap op de langste losse woorden en vergelijken we zelf. 0 = uit.
FALLBACK_WORDS = 2
CACHE_SECONDS = 10 * 60
ANILIST_URL = "https://graphql.anilist.co"
SEARCH_TIMEOUT = 10.0

QUERY = """
query ($search: String) {
  Page(perPage: 25) {
    media(search: $search, type: MANGA) {
      title { romaji english native }
      synonyms
      coverImage { extraLarge large }
    }
  }
}
"""

_ARTICLES = ("the ", "a ", "an ")
_COMMON_WORDS = {"with", "from", "that", "this", "your", "into", "over", "when", "after", "about"}


def normalize(title: str) -> str:
    """'  Omniscient Reader's  Viewpoint!' -> 'omniscient readers viewpoint'"""
    text = unicodedata.normalize("NFKC", title).lower()
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^\w\s]|_", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _without_article(text: str) -> str:
    for article in _ARTICLES:
        if text.startswith(article):
            return text[len(article):]
    return text


def search_words(title: str) -> list[str]:
    """De langste woorden die op zichzelf iets zeggen: 'Tower of Godd' -> ['tower', 'godd']."""
    words = {w for w in normalize(title).split() if len(w) >= 4 and w not in _COMMON_WORDS}
    return sorted(words, key=lambda w: (-len(w), w))[:FALLBACK_WORDS]


def similarity(mine: str, other: str) -> float:
    a, b = normalize(mine), normalize(other)
    if not a or not b:
        return 0.0
    return max(fuzz.ratio(a, b), fuzz.ratio(_without_article(a), _without_article(b)))


@dataclass(frozen=True)
class CoverCandidate:
    title: str
    image_url: str
    score: float


def rank(query: str, results: list[dict]) -> list[CoverCandidate]:
    """Alleen resultaten boven de drempel, beste match eerst."""
    candidates = {}
    for media in results:
        names = media.get("title") or {}
        titles = [names.get("english"), names.get("romaji"), names.get("native"), *(media.get("synonyms") or [])]
        titles = [t for t in titles if t]
        images = media.get("coverImage") or {}
        image = images.get("extraLarge") or images.get("large")
        if not titles or not image:
            continue
        score = max(similarity(query, t) for t in titles)
        if score >= COVER_MATCH_THRESHOLD and score > candidates.get(image, CoverCandidate("", "", -1)).score:
            candidates[image] = CoverCandidate(titles[0], image, score)
    return sorted(candidates.values(), key=lambda c: c.score, reverse=True)


class CoverSearch:
    def __init__(self, client: httpx.Client, clock=time.monotonic):
        self._client = client
        self._clock = clock
        self._cache: dict[str, tuple[float, list[CoverCandidate]]] = {}

    def search(self, title: str) -> list[CoverCandidate]:
        key = normalize(title)
        if not key:
            return []
        now = self._clock()
        cached = self._cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]

        # 1. de titel zoals getypt, 2. genormaliseerd, 3. losse woorden.
        results = rank(title, self._ask_anilist(title.strip()))
        if not results and key != title.strip():
            results = rank(title, self._ask_anilist(key))
        for word in search_words(title):
            if results:
                break  # zuinig met AniList: stoppen zodra er iets past
            results = rank(title, self._ask_anilist(word))

        self._cache = {k: v for k, v in self._cache.items() if now - v[0] < CACHE_SECONDS}
        self._cache[key] = (now, results)
        return results

    def _ask_anilist(self, term: str) -> list[dict]:
        try:
            response = self._client.post(
                ANILIST_URL,
                json={"query": QUERY, "variables": {"search": term}},
                timeout=SEARCH_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise CoverError("AniList took too long to answer. Try again.") from exc
        except httpx.HTTPError as exc:
            raise CoverError("Couldn't reach AniList. Check your internet connection.") from exc
        if response.status_code == 429:
            raise CoverError("AniList is busy right now. Try again in a minute.")
        if response.status_code != 200:
            raise CoverError(f"AniList gave an error (HTTP {response.status_code})")
        try:
            return response.json()["data"]["Page"]["media"] or []
        except (ValueError, KeyError, TypeError) as exc:
            raise CoverError("AniList sent an answer Progen doesn't understand") from exc
