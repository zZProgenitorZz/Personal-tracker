"""Elke coverbron apart: wat vraagt hij, en wat haalt hij uit het antwoord?
Alle API's zijn nep (httpx.MockTransport)."""
import json

import httpx
import pytest

from app.covers import CoverError
from app.reading import cover_sources
from app.reading.cover_sources import anilist, google_books, mangaupdates, myanimelist, open_library


def client_answering(answer, seen=None):
    def handle(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    return httpx.Client(transport=httpx.MockTransport(handle))


# ---- AniList ----

def test_anilist_reads_all_titles_and_largest_cover():
    seen = []
    client = client_answering({"data": {"Page": {"media": [
        {"title": {"romaji": "Ore dake Level Up na Ken", "english": "Solo Leveling", "native": "나 혼자만 레벨업"},
         "synonyms": ["Only I Level Up"], "coverImage": {"extraLarge": "https://a/xl.jpg", "large": "https://a/l.jpg"}},
        {"title": {"romaji": "Zonder cover"}, "synonyms": [], "coverImage": {}},
    ]}}}, seen)

    [result] = anilist(client, "Solo Leveling")

    assert result.source == "AniList"
    assert result.image_url == "https://a/xl.jpg"
    assert set(result.titles) == {"Solo Leveling", "Ore dake Level Up na Ken", "나 혼자만 레벨업", "Only I Level Up"}
    body = json.loads(seen[0].content)
    assert body["variables"]["search"] == "Solo Leveling" and "type: MANGA" in body["query"]


# ---- MangaUpdates ----

def test_mangaupdates_reads_novels_too():
    seen = []
    client = client_answering({"results": [
        {"hit_title": "Shadow Slave", "record": {"title": "Shadow Slave (Novel)", "type": "Novel",
                                                 "image": {"url": {"original": "https://mu/o.jpg", "thumb": "https://mu/t.jpg"}}}},
        {"hit_title": "Zonder plaatje", "record": {"title": "Zonder plaatje", "type": "Manhwa", "image": {"url": {}}}},
    ]}, seen)

    [result] = mangaupdates(client, "Shadow Slave")

    assert result.source == "MangaUpdates"
    assert result.image_url == "https://mu/o.jpg"
    assert set(result.titles) == {"Shadow Slave (Novel)", "Shadow Slave"}
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content)["search"] == "Shadow Slave"
    assert "type" not in json.loads(seen[0].content)  # geen typefilter: novels én manhwa


# ---- Google Books ----

def test_google_books_asks_for_largest_version_over_https(monkeypatch):
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    seen = []
    client = client_answering({"items": [
        {"volumeInfo": {"title": "Shadow Slave", "subtitle": "Book 1", "imageLinks": {
            "smallThumbnail": "http://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=5&edge=curl",
            "thumbnail": "http://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=1&edge=curl",
        }}},
        {"volumeInfo": {"title": "Zonder plaatje"}},
    ]}, seen)

    [result] = google_books(client, "Shadow Slave")

    assert result.source == "Google Books"
    assert result.image_url == "https://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=0"
    assert result.check_size  # Google-thumbnails kunnen te klein zijn
    assert set(result.titles) == {"Shadow Slave", "Shadow Slave: Book 1"}
    assert seen[0].url.params["q"] == "intitle:Shadow Slave"
    assert "key" not in seen[0].url.params


def test_google_books_prefers_extra_large_link():
    client = client_answering({"items": [{"volumeInfo": {"title": "A", "imageLinks": {
        "thumbnail": "https://g/t?zoom=1", "extraLarge": "https://g/xl?zoom=6"}}}]})
    assert google_books(client, "A")[0].image_url == "https://g/xl?zoom=6"


def test_google_books_uses_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "geheim")
    seen = []
    google_books(client_answering({"totalItems": 0}, seen), "Shadow Slave")
    assert seen[0].url.params["key"] == "geheim"


def test_google_books_error_never_shows_the_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "geheim")
    with pytest.raises(CoverError) as error:
        google_books(client_answering(httpx.Response(403)), "Shadow Slave")
    assert "geheim" not in str(error.value)


# ---- Open Library ----

def test_open_library_builds_large_cover_url():
    seen = []
    client = client_answering({"docs": [
        {"title": "Shadow Slave", "alternative_title": ["Shadow Slave Vol. 1"], "cover_i": 15100918},
        {"title": "Zonder cover"},
    ]}, seen)

    [result] = open_library(client, "Shadow Slave")

    assert result.source == "Open Library"
    assert result.image_url == "https://covers.openlibrary.org/b/id/15100918-L.jpg"
    assert set(result.titles) == {"Shadow Slave", "Shadow Slave Vol. 1"}
    assert seen[0].url.params["title"] == "Shadow Slave"


# ---- Fouten zijn voor elke bron hetzelfde ----

SOURCES = [(anilist, "AniList"), (mangaupdates, "MangaUpdates"), (myanimelist, "MyAnimeList"),
           (google_books, "Google Books"), (open_library, "Open Library")]


@pytest.mark.parametrize("source, name", SOURCES)
def test_rate_limit_names_the_source(source, name):
    with pytest.raises(CoverError, match=f"{name} is busy"):
        source(client_answering(httpx.Response(429)), "x")


@pytest.mark.parametrize("source, name", SOURCES)
def test_timeout_names_the_source(source, name):
    with pytest.raises(CoverError, match=f"{name} took too long"):
        source(client_answering(httpx.ReadTimeout("traag")), "x")


@pytest.mark.parametrize("source, name", SOURCES)
def test_network_error_names_the_source(source, name):
    with pytest.raises(CoverError, match=f"reach {name}"):
        source(client_answering(httpx.ConnectError("offline")), "x")


@pytest.mark.parametrize("source, name", SOURCES)
def test_unreadable_answer_names_the_source(source, name):
    with pytest.raises(CoverError, match=name):
        source(client_answering(httpx.Response(200, text="<html>")), "x")


# ---- Genres die bronnen meegeven ----

def test_anilist_genres_and_strong_tags():
    seen = []
    client = client_answering({"data": {"Page": {"media": [{
        "title": {"english": "Lord of Mysteries"}, "synonyms": [], "coverImage": {"extraLarge": "https://a/x.jpg"},
        "genres": ["Action", "Mystery", "Ecchi"],
        "tags": [{"name": "Isekai", "rank": 70}, {"name": "Martial Arts", "rank": 20}],
    }]}}}, seen)
    [result] = anilist(client, "Lord of Mysteries")
    assert result.genres == ("Action", "Isekai", "Mystery")  # zwakke tag en onbekend genre vallen weg
    assert "genres" in json.loads(seen[0].content)["query"]


def test_mangaupdates_genres():
    client = client_answering({"results": [{"hit_title": "Reverend Insanity", "record": {
        "title": "Reverend Insanity", "image": {"url": {"original": "https://mu/o.jpg"}},
        "genres": [{"genre": "Action"}, {"genre": "Martial Arts"}, {"genre": "Seinen"}]}}]})
    assert mangaupdates(client, "Reverend Insanity")[0].genres == ("Action", "Martial Arts")


def test_google_books_categories(monkeypatch):
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    client = client_answering({"items": [{"volumeInfo": {
        "title": "Shadow Slave", "categories": ["Fiction / Fantasy / Epic"],
        "imageLinks": {"thumbnail": "http://g/t?zoom=1"}}}]})
    assert google_books(client, "Shadow Slave")[0].genres == ("Fantasy",)


def test_open_library_subjects():
    seen = []
    client = client_answering({"docs": [{"title": "Shadow Slave", "cover_i": 1, "subject": ["Fantasy fiction", "Magic"]}]}, seen)
    assert open_library(client, "Shadow Slave")[0].genres == ("Fantasy",)
    assert "subject" in seen[0].url.params["fields"]


# ---- Novels worden niet weggefilterd ----

def test_anilist_keeps_novels():
    seen = []
    client = client_answering({"data": {"Page": {"media": [
        {"format": "NOVEL", "title": {"english": "Lord of the Mysteries", "romaji": "Guimi Zhi Zhu"},
         "synonyms": [], "coverImage": {"extraLarge": "https://a/novel.jpg"}},
        {"format": "MANGA", "title": {"english": "Lord of the Mysteries"}, "synonyms": [],
         "coverImage": {"extraLarge": "https://a/manhua.jpg"}},
    ]}}}, seen)
    assert [r.image_url for r in anilist(client, "Lord of the Mysteries")] == ["https://a/novel.jpg", "https://a/manhua.jpg"]
    query = json.loads(seen[0].content)["query"]
    assert "type: MANGA" in query and "format" not in query.split("media(", 1)[1].split(")", 1)[0]  # geen formatfilter


def test_mangaupdates_matches_on_the_associated_name_it_found():
    # Zoek je "Coiling Dragon", dan heet de serie zelf "Panlong"; hit_title is de naam die matchte.
    client = client_answering({"results": [{"hit_title": "Coiling Dragon", "record": {
        "title": "Panlong", "type": "Manhua", "image": {"url": {"original": "https://mu/p.jpg"}}}}]})
    [result] = mangaupdates(client, "Coiling Dragon")
    assert "Coiling Dragon" in result.titles and "Panlong" in result.titles


# ---- MyAnimeList (via Jikan) ----

def jikan_novel(mal_id, title, *, english=None, synonyms=(), image="https://mal/x.jpg", type_="Light Novel",
                genres=(), themes=()):
    return {"mal_id": mal_id, "title": title, "title_english": english, "title_japanese": None,
            "title_synonyms": list(synonyms), "titles": [{"type": "Default", "title": title}],
            "type": type_, "images": {"jpg": {"image_url": image + "?small", "large_image_url": image}},
            "genres": [{"name": g} for g in genres], "themes": [{"name": t} for t in themes]}


@pytest.fixture
def no_jikan_wait(monkeypatch):
    monkeypatch.setattr(cover_sources, "JIKAN_MIN_INTERVAL", 0)


def test_myanimelist_asks_for_novels_and_light_novels(no_jikan_wait):
    seen = []
    client = client_answering({"data": [jikan_novel(1, "Gu Zhen Ren", english="Reverend Insanity",
                                                    genres=["Action", "Fantasy"], themes=["Martial Arts", "Reincarnation"])]}, seen)
    [result] = myanimelist(client, "Reverend Insanity")  # zelfde id in beide antwoorden: één keer
    assert [r.url.params["type"] for r in seen] == ["novel", "lightnovel"]
    assert all(r.url.host == "api.jikan.moe" and r.url.params["q"] == "Reverend Insanity" for r in seen)
    assert (result.source, result.image_url) == ("MyAnimeList", "https://mal/x.jpg")
    assert set(result.titles) >= {"Gu Zhen Ren", "Reverend Insanity"}
    assert result.genres == ("Action", "Fantasy", "Martial Arts", "Reincarnation")


def test_myanimelist_uses_every_title(no_jikan_wait):
    novel = jikan_novel(2, "Wo Yu Feng Tian", english="I Shall Seal the Heavens", synonyms=["ISSTH"])
    novel["titles"].append({"type": "Chinese", "title": "我欲封天"})
    [result] = myanimelist(client_answering({"data": [novel]}), "ISSTH")
    assert set(result.titles) == {"Wo Yu Feng Tian", "I Shall Seal the Heavens", "ISSTH", "我欲封天"}


def test_myanimelist_rate_limit_keeps_what_it_already_found(no_jikan_wait):
    answers = iter([httpx.Response(200, json={"data": [jikan_novel(1, "Coiling Dragon")]}), httpx.Response(429)])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: next(answers)))
    assert [r.titles[0] for r in myanimelist(client, "Coiling Dragon")] == ["Coiling Dragon"]


def test_myanimelist_rate_limit_without_results_skips_the_source(no_jikan_wait):
    with pytest.raises(CoverError, match="MyAnimeList is busy"):
        myanimelist(client_answering(httpx.Response(429)), "x")


def test_myanimelist_waits_between_requests(monkeypatch):
    monkeypatch.setattr(cover_sources, "JIKAN_MIN_INTERVAL", 0.2)
    import time
    started = time.monotonic()
    myanimelist(client_answering({"data": []}), "x")
    myanimelist(client_answering({"data": []}), "x")
    assert time.monotonic() - started >= 0.55  # 3 pauzes tussen 4 verzoeken (Jikan: max 3 per seconde)


def test_unreachable_myanimelist_is_skipped_for_a_while(no_jikan_wait, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cover_sources.time, "monotonic", lambda: clock[0])
    seen = []
    with pytest.raises(CoverError, match="MyAnimeList took too long"):
        myanimelist(client_answering(httpx.ConnectTimeout("geen verbinding"), seen), "x")
    with pytest.raises(CoverError, match="unreachable"):
        myanimelist(client_answering({"data": []}, seen), "x")  # meteen, zonder verzoek
    assert len(seen) == 1

    clock[0] += cover_sources.JIKAN_COOLDOWN + 1
    assert myanimelist(client_answering({"data": []}, seen), "x") == []  # daarna weer gewoon proberen
    assert len(seen) == 3


def test_myanimelist_uses_a_short_connect_timeout(no_jikan_wait):
    seen = []
    myanimelist(client_answering({"data": []}, seen), "x")
    assert seen[0].extensions["timeout"]["connect"] == cover_sources.JIKAN_CONNECT_TIMEOUT
