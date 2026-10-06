import json

import httpx
import pytest

from app.covers import CoverError
from app.reading.cover_search import COVER_MATCH_THRESHOLD, CoverSearch, normalize, rank, similarity


def media(romaji, english=None, native=None, synonyms=(), image="https://img.anili.st/x.jpg"):
    return {
        "title": {"romaji": romaji, "english": english, "native": native},
        "synonyms": list(synonyms),
        "coverImage": {"extraLarge": image, "large": image},
    }


def anilist(pages: dict[str, list] | None = None, status: int = 200):
    """Nep-AniList: geeft per zoekterm een vaste lijst terug en onthoudt de zoektermen."""
    searched = []

    def handle(request: httpx.Request):
        term = json.loads(request.content)["variables"]["search"]
        searched.append(term)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"data": {"Page": {"media": (pages or {}).get(term, [])}}})

    return CoverSearch(httpx.Client(transport=httpx.MockTransport(handle))), searched


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
])
def test_small_differences_match(mine, official):
    assert similarity(mine, official) >= COVER_MATCH_THRESHOLD


@pytest.mark.parametrize("mine, other", [
    ("Tower of God", "The God of High School"),
    ("Solo Leveling", "Solo Leveling: Ragnarok"),
    ("Nano Machine", "Machine Heart"),
    ("Shadow Slave", "Slave of the Shadow Kingdom"),
])
def test_different_titles_do_not_match(mine, other):
    assert similarity(mine, other) < COVER_MATCH_THRESHOLD


def test_rank_uses_every_title_and_sorts_best_first():
    results = rank("Solo Leveling", [
        media("Ore dake Level Up na Ken", english="Solo Leveling", image="https://a/1.jpg"),
        media("Unrelated", english="The God of High School", image="https://a/2.jpg"),
        media("Na Honjaman Level Up", synonyms=["Solo Levelling"], image="https://a/3.jpg"),
        media("Solo Leveling", image=None),  # zonder cover: overslaan
    ])
    assert [r.image_url for r in results] == ["https://a/1.jpg", "https://a/3.jpg"]
    assert results[0].score >= results[1].score


# ---- Zoeken via AniList ----

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
    # AniList vindt zelf niets bij een typfout, maar wel op een los, goed gespeld woord.
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


def test_results_are_cached_per_title():
    search, searched = anilist({"Solo Leveling": [media("Solo Leveling")]})
    search.search("Solo Leveling")
    search.search("  solo leveling ")
    assert searched == ["Solo Leveling"]


def test_rate_limit_gives_clear_error():
    search, _ = anilist(status=429)
    with pytest.raises(CoverError, match="busy"):
        search.search("Solo Leveling")


def test_network_error_gives_clear_error():
    def offline(request):
        raise httpx.ConnectError("geen netwerk", request=request)

    search = CoverSearch(httpx.Client(transport=httpx.MockTransport(offline)))
    with pytest.raises(CoverError, match="reach AniList"):
        search.search("Solo Leveling")
