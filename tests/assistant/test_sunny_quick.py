"""Chapters without the API (quick_log), Undo instead of Confirm, misheard titles and nicknames.
Fake clients only; a quick log must never call the API."""
import datetime as dt
from datetime import datetime, timezone

import pytest
from fakes import FakeClient, response, say, stream_lines, text, tool
from fastapi.testclient import TestClient

import app.clock as clock
from app.assistant.commands import AddAlias
from app.assistant.events import AliasAdded
from app.assistant.projections import AliasProjection
from app.assistant.quick import quick_log, sound_key
from app.assistant.tools import QUICK_SAVED, run_tool
from app.main import create_app
from app.reading.commands import StartSeries
from app.reading.events import Kind, SeriesStarted, Status, StatusChanged
from app.reading.projections import LibraryProjection

AT = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def library(*series) -> LibraryProjection:
    """(id, title, chapter[, status]) -> a library projection."""
    projection = LibraryProjection()
    for sid, title, chapter, *status in series:
        projection.apply(SeriesStarted(sid, title, Kind.MANHWA, "x", chapter, at=AT))
        if status:
            projection.apply(StatusChanged(sid, Status.READING, status[0], at=AT))
    return projection


SHELF = library(("solo", "Solo Leveling", 119), ("orv", "Omniscient Reader's Viewpoint", 551),
                ("lotm", "Lord of the Mysteries", 400), ("tog", "Tower of God", 580, Status.ON_HOLD))


def logged(*texts, shelf=SHELF, aliases=None):
    hit = quick_log(list(texts), shelf, aliases)
    return (hit.title, hit.old, hit.new) if hit else None


# ---- quick_log ----

@pytest.mark.parametrize("said", [
    "Solo Leveling 120", "120 Solo Leveling", "ik ben bij hoofdstuk 120 van Solo Leveling", "hoofdstuk 120 solo leveling",
    "I'm at chapter 120 of Solo Leveling", "read Solo Leveling up to 120", "Solo Leveling chapter 120",
    "solo leveling honderdtwintig", "Solo Leveling one hundred twenty", "hundred and twenty of solo leveling"])
def test_dutch_and_english_sentences_and_numbers_in_words(said):
    assert logged(said) == ("Solo Leveling", 119, 120)


def test_misheard_titles_still_match_by_sound():
    assert logged("solo leaving 120") == ("Solo Leveling", 119, 120)
    assert logged("omniscient readers few point 560") == ("Omniscient Reader's Viewpoint", 551, 560)
    assert logged("lord of the mysteries 412") == ("Lord of the Mysteries", 400, 412)  # "of" stays in the title


def test_not_sure_enough_is_none():
    twins = library(("a", "Tower of God", 10), ("b", "Tower of Gods", 10))
    assert logged("tower of god 12", shelf=twins) is None  # two titles too close together
    assert logged("bananas 120") is None                    # nothing like it
    assert logged("what did I read this week") is None      # no number
    assert logged("solo leveling 120 and lord of the mysteries 401") is None  # two numbers


def test_back_the_same_or_a_big_jump_is_for_claude():
    assert logged("solo leveling 118") is None
    assert logged("solo leveling 119") is None
    assert logged("solo leveling 170") is None   # more than 50 ahead
    assert logged("solo leveling 169") == ("Solo Leveling", 119, 169)


def test_the_alternatives_are_tried_too():
    assert logged("so low living one twenty", "solo leveling 120") == ("Solo Leveling", 119, 120)


def test_a_nickname_wins():
    aliases = AliasProjection()
    aliases.apply(AliasAdded("toaster god", "tog"))
    assert logged("toaster god 581", aliases=aliases) == ("Tower of God", 580, 581)
    assert sound_key("Toaster  God!") == sound_key("toaster god")


# ---- In the app ----

@pytest.fixture
def app_and_client(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: dt.datetime(2026, 10, 11, 10, 0))
    app = create_app(":memory:")
    client = TestClient(app, headers={"HX-Request": "true"})
    for title, chapter in [("Solo Leveling", "119"), ("Shadow Slave", "100")]:
        client.post("/ui/reading/series", data={"title": title, "kind": "manhwa", "source": "x", "start_chapter": chapter})
    return app, client


def chapter(client, title="Solo Leveling") -> float:
    return next(s["current_chapter"] for s in client.get("/reading/library").json() if s["title"] == title)


def undo_id(html: str) -> str:
    return html.split('hx-post="/ui/assistant/undo/', 1)[1].split('"', 1)[0]


def test_a_quick_log_is_saved_without_calling_the_api(app_and_client):
    app, client = app_and_client
    fake = FakeClient()  # it would fail if anything asked it
    app.state.sunny.client = fake
    lines = stream_lines(client, "Solo Leveling 120")
    assert fake.calls == [] and chapter(client) == 120
    assert lines[-1]["changed"] == ["reading-changed"] and "Undo" in lines[-1]["html"]
    assert "Solo Leveling" in lines[-1]["html"] and "120" in lines[-1]["html"]
    assert "[Quick log: Solo Leveling 119 → 120, saved" in app.state.sunny.history[-1]["content"]


def test_quick_logs_work_without_a_key_and_the_rest_says_so(app_and_client):
    _, client = app_and_client  # in the tests there is never a real client
    assert "Undo" in say(client, "ik ben bij hoofdstuk 120 van Solo Leveling") and chapter(client) == 120
    assert "ANTHROPIC_API_KEY" in say(client, "what did I read this week")


def test_undo_sets_it_back_once_and_the_button_goes(app_and_client):
    _, client = app_and_client
    html = say(client, "Solo Leveling 120")
    undone = client.post(f"/ui/assistant/undo/{undo_id(html)}")
    assert "Undone. Back to 119." in undone.text and 'hx-swap-oob="delete"' in undone.text
    assert undone.headers["HX-Trigger"] == "reading-changed" and chapter(client) == 119
    again = client.post(f"/ui/assistant/undo/{undo_id(html)}")
    assert "Nothing to undo" in again.text and chapter(client) == 119
    assert "data-sunny-undo" not in client.get("/ui/assistant").text


def test_only_the_last_log_can_be_undone(app_and_client):
    _, client = app_and_client
    first = undo_id(say(client, "Solo Leveling 120"))
    second = undo_id(say(client, "Solo Leveling 121"))
    assert "Nothing to undo" in client.post(f"/ui/assistant/undo/{first}").text
    assert "Back to 120" in client.post(f"/ui/assistant/undo/{second}").text


def test_with_the_setting_off_it_goes_through_claude_and_confirm(app_and_client):
    app, client = app_and_client
    client.post("/ui/assistant/quick-saves", data={"enabled": ""})
    sid = app.state.sunny.data.library.find_by_title("Solo Leveling").series_id
    fake = FakeClient(response(tool("set_reading_progress", {"series_id": sid, "chapter": 120})),
                      response(text("Chapter 120. Confirm it.")))
    app.state.sunny.client = fake
    html = say(client, "Solo Leveling 120")
    assert len(fake.calls) == 2 and 'hx-post="/ui/assistant/confirm/' in html and "data-sunny-undo" not in html
    assert fake.calls[1]["messages"][-1]["content"][0]["content"] == "proposed, waiting for Progen's confirmation"
    assert chapter(client) == 119


def test_claudes_only_small_chapter_step_is_saved_with_undo(app_and_client):
    app, client = app_and_client
    sid = app.state.sunny.data.library.find_by_title("Solo Leveling").series_id
    fake = FakeClient(response(tool("set_reading_progress", {"series_id": sid, "chapter": 120})),
                      response(text("One more chapter. Saved; undo if that was wrong.")))
    app.state.sunny.client = fake
    html = say(client, "I read another chapter of the one about the hunter")
    assert fake.calls[1]["messages"][-1]["content"][0]["content"] == QUICK_SAVED
    assert "data-sunny-undo" in html and "/ui/assistant/confirm/" not in html and chapter(client) == 120
    assert "[Saved right away: Solo Leveling · chapter 119 → 120" in app.state.sunny.history[-1]["content"]


def test_with_a_second_action_it_is_a_card_again(app_and_client):
    app, client = app_and_client
    sid = app.state.sunny.data.library.find_by_title("Solo Leveling").series_id
    app.state.sunny.client = FakeClient(
        response(tool("set_reading_progress", {"series_id": sid, "chapter": 120}, "a"),
                 tool("add_plan", {"text": "elke ma 12:00 gym"}, "b")),
        response(text("Two things. Confirm.")))
    html = say(client, "the hunter one, one more, and gym on mondays")
    assert 'hx-post="/ui/assistant/confirm/' in html and "data-sunny-undo" not in html and chapter(client) == 119


def test_the_model_hears_what_else_was_heard(app_and_client):
    app, client = app_and_client
    fake = FakeClient(response(text("Which one?")))
    app.state.sunny.client = fake
    client.post("/ui/assistant/stream", data={"message": "what about solo leaving", "voice": "1",
                                              "alts": '["what about solo leveling", "what about solo leaving", "x"]'})
    sent = fake.calls[-1]["messages"][-1]["content"]
    assert sent == '(voice; also heard: "what about solo leveling" | "x") what about solo leaving'


# ---- Misheard titles and nicknames ----

def test_find_item_gives_a_score_and_hears_titles_that_sound_alike(app_and_client):
    app, _ = app_and_client
    found, _ = run_tool("find_item", {"query": "solo leaving", "domain": "reading"}, app.state.sunny.data)
    assert found[0]["title"] == "Solo Leveling" and 85 <= found[0]["score"] <= 100
    assert all(0 <= row["score"] <= 100 for row in found)


def test_an_undone_quick_log_teaches_a_nickname(app_and_client):
    app, client = app_and_client
    html = say(client, "solo leaving 120")       # heard as Solo Leveling ...
    assert chapter(client) == 120
    client.post(f"/ui/assistant/undo/{undo_id(html)}")
    say(client, "Shadow Slave 120")      # ... but Progen meant this one
    newbie = app.state.sunny.data.library.find_by_title("Shadow Slave").series_id
    assert app.state.sunny.data.aliases.series_for("solo leaving") == newbie
    say(client, "solo leaving 121")               # from now on the nickname wins
    assert chapter(client, "Shadow Slave") == 121 and chapter(client) == 119
    settings = client.get("/ui/assistant/settings").text
    assert "&#34;solo leaving&#34; → Shadow Slave" in settings or '"solo leaving" → Shadow Slave' in settings
    client.post("/ui/assistant/aliases/remove", data={"heard": "solo leaving"})
    assert app.state.sunny.data.aliases.series_for("solo leaving") is None


def test_remember_alias_is_a_strict_write_tool_with_confirm(app_and_client):
    app, client = app_and_client
    data = app.state.sunny.data
    newbie = data.library.find_by_title("Shadow Slave").series_id
    result, pending = run_tool("remember_alias", {"heard": "so low max", "series_id": newbie}, data)
    assert result == "proposed, waiting for Progen's confirmation" and pending.label == 'Nickname · "so low max" → Shadow Slave'
    assert data.aliases.series_for("so low max") is None  # nothing until Confirm
    app.state.sunny.client = FakeClient(response(tool("remember_alias", {"heard": "so low max", "series_id": newbie})),
                                        response(text("Say yes and I'll remember it.")))
    card = say(client, "remember that so low max is the shadow one")
    proposal = card.split('id="sunny-card-', 1)[1].split('"', 1)[0]
    client.post(f"/ui/assistant/confirm/{proposal}")
    assert data.aliases.series_for("so low max") == newbie


def test_nicknames_survive_a_restart(tmp_path):
    def app():
        return create_app(str(tmp_path / "tracker.db"), backup_dir=tmp_path / "backups", env_file=tmp_path / "no.env")
    first = app()
    [started] = first.state.sunny.data.reading.handle(StartSeries("Solo Leveling", Kind.MANHWA, "x", 119))
    first.state.sunny.settings.handle(AddAlias("solo leaving", started.series_id))
    again = app()
    assert again.state.sunny.data.aliases.series_for("Solo Leaving") == started.series_id
