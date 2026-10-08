"""Zoeken over alle bronnen: normaliseren, fuzzy matching, combineren en cachen.
Bronnen zijn hier nepfuncties of een nep-AniList; er gaat niets over het netwerk."""
import json
import time
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.covers import CoverError
from app.cover_search import (
    COVER_MATCH_THRESHOLD, MIN_SOURCE_SIZE, CoverSearch, normalize, rank, required_score, similarity,
)
from app.reading.cover_sources import SourceResult, anilist as anilist_source


def media(romaji, english=None, native=None, synonyms=(), image="https://img.anili.st/x.jpg"):
    return {
        "title": {"romaji": romaji, "english": english, "native": native},
        "synonyms": list(synonyms),
        "coverImage": {"extraLarge": image, "large": image},
    }


def anilist(pages: dict[str, list] | None = None, status: int = 200):
    """Nep-AniList als enige bron: geeft per zoekterm een vaste lijst terug en onthoudt de zoektermen."""
    searched = []

    def handle(request: httpx.Request):
        term = json.loads(request.content)["variables"]["search"]
        searched.append(term)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"data": {"Page": {"media": (pages or {}).get(term, [])}}})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return CoverSearch(client, sources=[anilist_source]), searched


def fake_source(name, results=(), *, error=None, delay=0.0, calls=None):
    """Een bron die meteen (of na `delay` seconden) vaste resultaten of een fout geeft."""
    def source(client, term):
        if calls is not None:
            calls.append((name, term))
        time.sleep(delay)
        if error:
            raise error
        return [SourceResult(tuple(titles), url, name) for titles, url in results]

    return source


def png(size) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, "teal").save(out, "PNG")
    return out.getvalue()


def no_network():
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))


# ---- Normaliseren ----

@pytest.mark.parametrize("raw, expected", [
    ("Solo Leveling", "solo leveling"),
    ("  Solo   LEVELING!! ", "solo leveling"),
    ("Omniscient Reader's Viewpoint", "omniscient readers viewpoint"),
    ("Re:Zero - Starting Life", "re zero starting life"),
    ("나 혼자만 레벨업", "나 혼자만 레벨업"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# ---- Fuzzy matching ----

@pytest.mark.parametrize("mine, official", [
    ("solo leveling", "Solo Leveling"),
    ("Omniscient Readers Viewpoint", "Omniscient Reader's Viewpoint"),
    ("Beginning After the End", "The Beginning After the End"),
    ("Tower of Godd", "Tower of God"),
    ("Lord of the Mysteries ", "Lord of Mysteries"),
    # achtervoegsels van bronnen tellen niet mee
    ("Shadow Slave", "Shadow Slave, Book 1"),
    ("Shadow Slave", "Shadow Slave (Novel)"),
    ("Lord of the Mysteries", "Lord of the Mysteries Vol. 2"),
    ("Solo Leveling", "Solo Leveling (Light Novel)"),
])
def test_small_differences_match(mine, official):
    assert similarity(mine, official) >= COVER_MATCH_THRESHOLD


@pytest.mark.parametrize("mine, other", [
    ("Tower of God", "The God of High School"),
    ("Frieren", "Frieden"),            # korte titels: één letter verschil is een andere serie
    ("Eleceed", "Elected"),
    ("Nano Machine", "Machine Heart"),
    ("Shadow Slave", "Slave of the Shadow Kingdom"),
    ("Shadow Slave", "Shadow Hack (Novel)"),
])
def test_different_titles_do_not_match(mine, other):
    assert similarity(mine, other) < required_score(mine)


def test_main_title_before_the_colon_counts():
    assert similarity("Frieren", "Frieren: Beyond Journey's End") >= required_score("Frieren")
    assert similarity("Re Zero", "Re:Zero - Starting Life in Another World") >= required_score("Re Zero")


def test_sequel_with_subtitle_ranks_below_the_exact_title():
    results = rank("Solo Leveling", [
        SourceResult(("Solo Leveling: Ragnarok",), "https://a/ragnarok.jpg", "AniList"),
        SourceResult(("Solo Leveling",), "https://a/solo.jpg", "AniList"),
    ])
    assert [r.image_url for r in results] == ["https://a/solo.jpg", "https://a/ragnarok.jpg"]


def test_short_title_does_not_pick_a_lookalike():
    results = rank("Frieren", [
        SourceResult(("Frieden",), "https://tv/frieden.jpg", "TVmaze"),
        SourceResult(("Frieren: Beyond Journey's End", "Sousou no Frieren"), "https://a/frieren.jpg", "AniList"),
    ])
    assert [r.image_url for r in results] == ["https://a/frieren.jpg"]


def test_equally_good_titles_show_the_first_one_of_the_source():
    [result] = rank("Frieren", [SourceResult(
        ("Frieren: Beyond Journey's End", "Frieren: Tras finalizar el viaje"), "https://a/f.jpg", "AniList")])
    assert result.title == "Frieren: Beyond Journey's End"


def test_title_that_only_matches_an_alternative_title_is_found():
    # Je kent hem als "ISSTH"; de bron noemt hem "Wo Yu Feng Tian", met ISSTH als synoniem.
    [result] = rank("I Shall Seal the Heavens", [
        SourceResult(("Wo Yu Feng Tian", "我欲封天", "I Shall Seal The Heavens"), "https://mal/issth.jpg", "MyAnimeList"),
        SourceResult(("Wo Yu Feng Tian 2",), "https://mal/other.jpg", "MyAnimeList"),
    ])
    assert result.image_url == "https://mal/issth.jpg" and result.score == 100
    assert result.title == "I Shall Seal The Heavens"


def test_failing_myanimelist_does_not_hide_other_sources():
    search = CoverSearch(no_network(), sources=[
        fake_source("MyAnimeList", error=CoverError("MyAnimeList is busy right now.")),
        fake_source("MangaUpdates", [(["Coiling Dragon (Novel)"], "https://mu/cd.jpg")]),
    ])
    [result] = search.search("Coiling Dragon")
    assert result.source == "MangaUpdates"


def test_rank_uses_every_title_and_sorts_best_first():
    results = rank("Solo Leveling", [
        SourceResult(("Ore dake Level Up na Ken", "Solo Leveling"), "https://a/1.jpg", "AniList"),
        SourceResult(("The God of High School",), "https://a/2.jpg", "AniList"),
        SourceResult(("Na Honjaman Level Up", "Solo Levelling"), "https://a/3.jpg", "AniList"),
    ])
    assert [r.image_url for r in results] == ["https://a/1.jpg", "https://a/3.jpg"]
    assert results[0].title == "Solo Leveling"  # de titel die het best past
    assert results[0].source == "AniList"


# ---- Stappen per bron: getypt, genormaliseerd, losse woorden ----

def test_search_returns_matching_covers():
    search, searched = anilist({"Solo Leveling": [media("Solo Leveling", image="https://a/sl.jpg")]})
    [result] = search.search("Solo Leveling")
    assert result.image_url == "https://a/sl.jpg"
    assert searched == ["Solo Leveling"]


def test_search_retries_once_with_normalized_title():
    search, searched = anilist({"omniscient readers viewpoint": [media("Omniscient Reader's Viewpoint")]})
    assert len(search.search("Omniscient Reader's Viewpoint!")) == 1
    assert searched == ["Omniscient Reader's Viewpoint!", "omniscient readers viewpoint"]


def test_search_falls_back_to_the_longest_words():
    search, searched = anilist({"tower": [media("Tower of God"), media("Tower Dungeon")]})
    [result] = search.search("Tower of Godd")
    assert result.title == "Tower of God"
    assert searched == ["Tower of Godd", "tower of godd", "tower"]


def test_search_without_matches_returns_nothing():
    search, searched = anilist({"Solo Leveling": [media("The God of High School")]})
    assert search.search("Solo Leveling") == []
    assert searched == ["Solo Leveling", "solo leveling", "leveling", "solo"]


def test_short_and_common_words_are_not_searched_alone():
    search, searched = anilist()
    search.search("The End of It")
    assert searched == ["The End of It", "the end of it"]


def test_each_source_stops_as_soon_as_it_matches():
    calls = []
    search = CoverSearch(no_network(), sources=[
        fake_source("A", [(["Shadow Slave"], "https://a/1.jpg")], calls=calls),
        fake_source("B", calls=calls),
    ])
    search.search("Shadow Slave")
    assert [term for name, term in calls if name == "A"] == ["Shadow Slave"]
    assert [term for name, term in calls if name == "B"] == ["Shadow Slave", "shadow slave", "shadow", "slave"]


# ---- Bronnen combineren ----

def test_results_of_all_sources_are_merged_and_sorted():
    search = CoverSearch(no_network(), sources=[
        fake_source("MangaUpdates", [(["Shadow Slave (Novel)"], "https://mu/1.jpg")]),
        fake_source("Open Library", [(["Shadow Slave, Book 1"], "https://ol/1.jpg"),
                                     (["Shadow Slav"], "https://ol/2.jpg")]),
        fake_source("AniList", [(["Shadow Slave"], "https://al/1.jpg")]),
    ])
    results = search.search("Shadow Slave")
    assert [r.source for r in results[:3]] == ["MangaUpdates", "Open Library", "AniList"]  # allemaal 100
    assert results[-1].image_url == "https://ol/2.jpg"  # typfout scoort lager
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_same_cover_url_is_shown_once():
    search = CoverSearch(no_network(), sources=[
        fake_source("A", [(["Shadow Slav"], "https://x/same.jpg")]),
        fake_source("B", [(["Shadow Slave"], "https://x/same.jpg")]),
    ])
    [result] = search.search("Shadow Slave")
    assert result.source == "B" and result.score == 100  # de beste match blijft over


def test_failing_source_does_not_hide_the_others():
    search = CoverSearch(no_network(), sources=[
        fake_source("AniList", error=CoverError("AniList is busy right now.")),
        fake_source("Open Library", [(["Shadow Slave"], "https://ol/1.jpg")]),
    ])
    [result] = search.search("Shadow Slave")
    assert result.source == "Open Library"


def test_crashing_source_does_not_hide_the_others():
    search = CoverSearch(no_network(), sources=[
        fake_source("Kapot", error=KeyError("onverwacht antwoord")),
        fake_source("Open Library", [(["Shadow Slave"], "https://ol/1.jpg")]),
    ])
    assert len(search.search("Shadow Slave")) == 1


def test_slow_source_is_skipped_after_its_deadline():
    search = CoverSearch(no_network(), deadline=0.3, sources=[
        fake_source("Traag", [(["Shadow Slave"], "https://slow/1.jpg")], delay=2),
        fake_source("Snel", [(["Shadow Slave"], "https://fast/1.jpg")]),
    ])
    started = time.monotonic()
    [result] = search.search("Shadow Slave")
    assert result.source == "Snel"
    assert time.monotonic() - started < 1.5


def test_all_sources_failing_gives_clear_error():
    search = CoverSearch(no_network(), sources=[
        fake_source("AniList", error=CoverError("AniList is busy right now.")),
        fake_source("Open Library", error=CoverError("Couldn't reach Open Library.")),
    ])
    with pytest.raises(CoverError, match="AniList is busy.*reach Open Library"):
        search.search("Shadow Slave")


def test_only_source_failing_keeps_its_own_message():
    search, _ = anilist(status=429)
    with pytest.raises(CoverError, match="^AniList is busy"):
        search.search("Solo Leveling")


def test_network_error_gives_clear_error():
    def offline(request):
        raise httpx.ConnectError("geen netwerk", request=request)

    search = CoverSearch(httpx.Client(transport=httpx.MockTransport(offline)), sources=[anilist_source])
    with pytest.raises(CoverError, match="reach AniList"):
        search.search("Solo Leveling")


# ---- Te kleine covers (Google Books) ----

def test_covers_that_are_too_small_are_skipped():
    def images(request):
        size = MIN_SOURCE_SIZE if "big" in request.url.path else (128, 190)
        return httpx.Response(200, content=png(size), headers={"Content-Type": "image/png"})

    def google(client, term):
        return [SourceResult(("Shadow Slave",), f"https://g/{name}.png", "Google Books", check_size=True)
                for name in ("tiny", "big", "broken-but-big")]

    search = CoverSearch(httpx.Client(transport=httpx.MockTransport(images)), sources=[google])
    assert [r.image_url for r in search.search("Shadow Slave")] == [
        "https://g/big.png", "https://g/broken-but-big.png"]


def test_size_is_only_checked_when_the_source_asks_for_it():
    requests = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(404)))
    search = CoverSearch(client, sources=[fake_source("AniList", [(["Shadow Slave"], "https://al/1.jpg")])])
    assert len(search.search("Shadow Slave")) == 1
    assert requests == []


def test_unreachable_image_counts_as_too_small():
    def google(client, term):
        return [SourceResult(("Shadow Slave",), "https://g/404.png", "Google Books", check_size=True)]

    search = CoverSearch(no_network(), sources=[google])
    assert search.search("Shadow Slave") == []


# ---- Cache ----

def test_results_are_cached_per_title():
    search, searched = anilist({"Solo Leveling": [media("Solo Leveling")]})
    search.search("Solo Leveling")
    search.search("  solo leveling ")
    assert searched == ["Solo Leveling"]


def test_results_with_a_failed_source_are_cached_only_briefly():
    clock = [0.0]
    calls = []
    search = CoverSearch(no_network(), clock=lambda: clock[0], sources=[
        fake_source("AniList", error=CoverError("AniList is busy right now."), calls=calls),
        fake_source("Open Library", [(["Shadow Slave"], "https://ol/1.jpg")], calls=calls),
    ])
    search.search("Shadow Slave")
    clock[0] = 30
    search.search("Shadow Slave")   # nog uit de cache: bladeren blijft stabiel
    first_round = len(calls)
    clock[0] = 90
    search.search("Shadow Slave")   # daarna de mislukte bron opnieuw proberen
    assert len(calls) > first_round
