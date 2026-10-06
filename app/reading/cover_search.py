"""Covers zoeken voor novels en manhwa, bij alle bronnen tegelijk.

Een hulpmiddel bij het invullen van het formulier: geen command en geen
event. Pas als je de serie opslaat, wordt de gekozen cover gedownload.
De bronnen zelf staan in cover_sources.py.
"""
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image, UnidentifiedImageError
from rapidfuzz import fuzz

from ..covers import CoverError
from .cover_sources import SOURCES, SOURCE_TIMEOUT, SourceResult

# Hoe streng de titels moeten overeenkomen (0–100). Lager vindt meer, maar
# ook vaker een verkeerde serie. 85 laat typfouten en een ontbrekend lidwoord
# door, maar geen vervolgdelen als "Solo Leveling: Ragnarok".
COVER_MATCH_THRESHOLD = 85
# Bronnen zoeken letterlijk: één typfout en ze vinden niets. Daarom zoeken we als
# laatste stap op de langste losse woorden en vergelijken we zelf. 0 = uit.
FALLBACK_WORDS = 2
# Zo lang mag één bron er in totaal over doen (alle stappen samen).
SOURCE_DEADLINE = 12.0
# Kleinere covers worden wazig als 300×450 (vooral Google-thumbnails).
MIN_SOURCE_SIZE = (200, 300)
CACHE_SECONDS = 10 * 60
CACHE_SECONDS_AFTER_FAILURE = 60  # kort, zodat een haperende bron snel weer meedoet

_ARTICLES = ("the ", "a ", "an ")
_COMMON_WORDS = {"with", "from", "that", "this", "your", "into", "over", "when", "after", "about"}
# Wat bronnen achter een titel zetten: "(Novel)", ", Book 1", "Vol. 2", ...
_SUFFIX = re.compile(
    r"""\s*(?:
        \((?:light\s*novel|web\s*novel|novel|ln|manhwa|manhua|manga|webtoon|comic)s?\)
      | [,:\-–]?\s*(?:book|vol\.?|volume|part|tome)\s*\d+
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def normalize(title: str) -> str:
    """'  Omniscient Reader's  Viewpoint!' -> 'omniscient readers viewpoint'"""
    text = unicodedata.normalize("NFKC", title).lower()
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^\w\s]|_", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def without_suffix(title: str) -> str:
    """'Shadow Slave, Book 1' -> 'Shadow Slave', 'Shadow Hack (Novel)' -> 'Shadow Hack'"""
    previous = None
    while previous != title:
        previous, title = title, _SUFFIX.sub("", title)
    return title


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
    a = normalize(mine)
    best = 0.0
    for variant in {normalize(other), normalize(without_suffix(other))}:
        if a and variant:
            best = max(best, fuzz.ratio(a, variant), fuzz.ratio(_without_article(a), _without_article(variant)))
    return best


@dataclass(frozen=True)
class CoverCandidate:
    title: str       # de titel van de bron die het best past
    image_url: str
    score: float
    source: str
    check_size: bool = False


def rank(query: str, results: list[SourceResult]) -> list[CoverCandidate]:
    """Alleen resultaten boven de drempel, beste match eerst, elke URL één keer."""
    best: dict[str, CoverCandidate] = {}
    for result in results:
        if not result.titles or not result.image_url:
            continue
        score, title = max((similarity(query, t), t) for t in result.titles)
        if score >= COVER_MATCH_THRESHOLD and score > best.get(result.image_url, _NONE).score:
            best[result.image_url] = CoverCandidate(title, result.image_url, score, result.source, result.check_size)
    # sorted is stabiel: bij gelijke score blijft de volgorde van de bronnen staan.
    return sorted(best.values(), key=lambda c: c.score, reverse=True)


_NONE = CoverCandidate("", "", -1, "")


class CoverSearch:
    def __init__(self, client: httpx.Client, *, sources=None, deadline: float = SOURCE_DEADLINE,
                 clock=time.monotonic):
        self._client = client
        self._sources = list(SOURCES if sources is None else sources)
        self._deadline = deadline
        self._clock = clock
        self._cache: dict[str, tuple[float, list[CoverCandidate]]] = {}  # titel -> (verloopt om, resultaten)

    def search(self, title: str) -> list[CoverCandidate]:
        key = normalize(title)
        if not key:
            return []
        now = self._clock()
        cached = self._cache.get(key)
        if cached and now < cached[0]:
            return cached[1]

        found, problems = self._ask_all_sources(title)
        if problems and len(problems) == len(self._sources):
            raise CoverError(" ".join(problems))  # alleen als niemand antwoordde

        results = rank(title, found)
        lifetime = CACHE_SECONDS_AFTER_FAILURE if problems else CACHE_SECONDS
        self._cache = {k: v for k, v in self._cache.items() if now < v[0]}
        self._cache[key] = (now + lifetime, results)
        return results

    # ---- Alle bronnen tegelijk, elk met een eigen deadline ----

    def _ask_all_sources(self, title: str) -> tuple[list[SourceResult], list[str]]:
        pool = ThreadPoolExecutor(max_workers=len(self._sources) or 1)
        futures = {pool.submit(self._search_source, source, title): source for source in self._sources}
        done, late = wait(futures, timeout=self._deadline)
        pool.shutdown(wait=False, cancel_futures=True)  # trage bronnen niet afwachten

        found, problems = [], []
        for future, source in futures.items():
            name = _source_name(source)
            if future in late:
                problems.append(f"{name} took too long to answer.")
            elif isinstance(future.exception(), CoverError):
                problems.append(str(future.exception()))
            elif future.exception() is not None:
                problems.append(f"{name} sent an answer Progen doesn't understand.")
            else:
                found += future.result()
        return found, problems

    def _search_source(self, source, title: str) -> list[SourceResult]:
        """Eén bron: 1. de titel zoals getypt, 2. genormaliseerd, 3. losse woorden.
        Stopt zodra iets past, om zuinig te zijn met de bron."""
        key = normalize(title)
        terms = [title.strip(), *([key] if key != title.strip() else []), *search_words(title)]
        for term in terms:
            matches = [c for c in rank(title, source(self._client, term)) if self._big_enough(c)]
            if matches:
                return [SourceResult((c.title,), c.image_url, c.source, c.check_size) for c in matches]
        return []

    def _big_enough(self, candidate: CoverCandidate) -> bool:
        if not candidate.check_size:
            return True
        try:
            response = self._client.get(candidate.image_url, timeout=SOURCE_TIMEOUT, follow_redirects=True)
            if response.status_code != 200:
                return False
            with Image.open(BytesIO(response.content)) as image:
                width, height = image.size
        except (httpx.HTTPError, UnidentifiedImageError, OSError):
            return False
        return width >= MIN_SOURCE_SIZE[0] and height >= MIN_SOURCE_SIZE[1]


def _source_name(source) -> str:
    return getattr(source, "source_name", None) or "A cover source"
