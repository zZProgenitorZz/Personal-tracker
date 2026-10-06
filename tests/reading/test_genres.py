"""De vaste genrelijst: genres uit bronnen vertalen, en genres die je zelf kiest controleren."""
import pytest

from app.reading.genres import GENRES, from_source, validated


def test_list_is_sorted_and_unique():
    assert GENRES == sorted(set(GENRES))


@pytest.mark.parametrize("names, expected", [
    (["Action", "Fantasy", "Mature", "Seinen"], ["Action", "Fantasy"]),          # MangaUpdates
    (["sci-fi", "School Life", "Slice of Life"], ["School Life", "Sci-Fi", "Slice of Life"]),
    (["Science Fiction", "Murim", "Xianxia"], ["Cultivation", "Martial Arts", "Sci-Fi"]),
    (["Fiction / Fantasy / Epic", "Fiction / Action & Adventure"], ["Action", "Adventure", "Fantasy"]),  # Google Books
    (["Fantasy fiction", "Magic"], ["Fantasy"]),                                 # Open Library
    (["Video Games", "Isekai", "Reincarnation"], ["Game", "Isekai", "Reincarnation"]),
    (["Fantasy", "fantasy", "FANTASY"], ["Fantasy"]),
    ([], []),
])
def test_genres_from_sources_are_mapped_to_the_list(names, expected):
    assert from_source(names) == expected


def test_chosen_genres_are_sorted_and_deduplicated():
    assert validated(["Romance", "Action", "Romance"]) == ["Action", "Romance"]


def test_unknown_genre_is_refused():
    with pytest.raises(ValueError, match="Ninja"):
        validated(["Action", "Ninja"])
