"""Covers (en genres) zoeken bij meerdere bronnen tegelijk; gedeeld door alle trackers.

Een hulpmiddel bij het invullen van een formulier: geen command en geen
event. Pas bij het opslaan wordt de gekozen cover gedownload.

Een bron is een functie `bron(client, zoekterm) -> list[SourceResult]` met
@cover_source("Naam") erboven. Elke tracker heeft zijn eigen bronnen, in
<tracker>/cover_sources.py, en geeft die mee aan CoverSearch.
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

from .covers import CoverError

SOURCE_TIMEOUT = 6.0  # seconden per verzoek


@dataclass(frozen=True)
class SourceResult:
    titles: tuple[str, ...]
    image_url: str
    source: str
    check_size: bool = False  # True: afmetingen controleren voordat we hem tonen
    genres: tuple[str, ...] = ()  # al vertaald naar de vaste genrelijst


def cover_source(name: str):
    """Geeft een bron zijn naam, zodat foutmeldingen hem kunnen noemen."""
    def register(function):
        function.source_name = name
        return function
    return register


def ask(client: httpx.Client, name: str, method: str, url: str, **kwargs):
    """Eén verzoek aan een bron, met nette foutmeldingen waarin de bron genoemd wordt."""
    try:
        response = client.request(method, url, timeout=SOURCE_TIMEOUT, **kwargs)
    except httpx.TimeoutException as exc:
        raise CoverError(f"{name} took too long to answer.") from exc
    except httpx.HTTPError as exc:
        raise CoverError(f"Couldn't reach {name}.") from exc
    if response.status_code == 429:
        raise CoverError(f"{name} is busy right now.")
    if response.status_code != 200:
        raise CoverError(f"{name} gave an error (HTTP {response.status_code}).")
    try:
        return response.json()
    except ValueError as exc:
        raise CoverError(f"{name} sent an answer Progen doesn't understand.") from exc


def unique_titles(*names) -> tuple[str, ...]:
    """Unieke, niet-lege titels in volgorde."""
    return tuple(dict.fromkeys(n.strip() for n in names if isinstance(n, str) and n.strip()))


# Hoe streng de titels moeten overeenkomen (0–100). Lager vindt meer, maar
# ook vaker een verkeerde serie. 85 laat typfouten en een ontbrekend lidwoord door.
COVER_MATCH_THRESHOLD = 85
# Korte titels (tot SHORT_TITLE tekens) strenger: bij "Frieren" is één letter
# verschil ("Frieden") al een andere serie.
SHORT_TITLE = 10
SHORT_TITLE_THRESHOLD = 92
# "Frieren" mag "Frieren: Beyond Journey's End" vinden via het deel vóór de dubbele
# punt, maar lager dan een exacte titel; zo staat een vervolg ("Solo Leveling:
# Ragnarok") altijd ná de serie zelf.
SUBTITLE_FACTOR = 0.92
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


def required_score(query: str) -> float:
    return SHORT_TITLE_THRESHOLD if len(normalize(query)) <= SHORT_TITLE else COVER_MATCH_THRESHOLD


def _main_titles(title: str) -> set[str]:
    """'Re:Zero - Starting Life' -> {'Re:Zero', 'Re'}; zonder ondertitel een lege set."""
    return {title.split(sep, 1)[0] for sep in (" - ", " – ", ":") if sep in title} - {""}


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return max(fuzz.ratio(a, b), fuzz.ratio(_without_article(a), _without_article(b)))


def similarity(mine: str, other: str) -> float:
    a = normalize(mine)
    full = max(_ratio(a, normalize(v)) for v in {other, without_suffix(other)})
    main = max((_ratio(a, normalize(v)) for v in _main_titles(without_suffix(other))), default=0.0)
    return max(full, main * SUBTITLE_FACTOR)


@dataclass(frozen=True)
class CoverCandidate:
    title: str       # de titel van de bron die het best past
    image_url: str
    score: float
    source: str
    check_size: bool = False
    genres: tuple[str, ...] = ()


def rank(query: str, results: list[SourceResult]) -> list[CoverCandidate]:
    """Alleen resultaten boven de drempel, beste match eerst, elke URL één keer."""
    best: dict[str, CoverCandidate] = {}
    required = required_score(query)
    for result in results:
        if not result.titles or not result.image_url:
            continue
        # Beste score; bij gelijke score de eerste titel van de bron (meestal de Engelse).
        score, _, title = max((similarity(query, t), -i, t) for i, t in enumerate(result.titles))
        if score >= required and score > best.get(result.image_url, _NONE).score:
            best[result.image_url] = CoverCandidate(
                title, result.image_url, score, result.source, result.check_size, result.genres)
    # sorted is stabiel: bij gelijke score blijft de volgorde van de bronnen staan.
    return sorted(best.values(), key=lambda c: c.score, reverse=True)


_NONE = CoverCandidate("", "", -1, "")


class CoverSearch:
    def __init__(self, client: httpx.Client, *, sources, deadline: float = SOURCE_DEADLINE,
                 clock=time.monotonic):
        self._client = client
        self._sources = list(sources)
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
                return [SourceResult((c.title,), c.image_url, c.source, c.check_size, c.genres) for c in matches]
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
