"""Sunny: prompt, tools and the agent loop. With a fake client, never the real API."""
import datetime as dt
import json

import pytest
from fakes import FakeClient, response, text, thinking, tool

from app.assistant import agent as agent_module
from app.assistant.agent import Agent, trim_history
from app.assistant.prompt import IDENTITY_DIR, system_blocks
from app.assistant.tools import TOOL_DEFINITIONS, ToolError, TrackerData, run_tool
from app.eventstore import EventStore
from app.listening.commands import ListeningCommandHandler, RecordPlay
from app.listening.projections import ListeningActivityProjection, TopArtistsProjection, TopTracksProjection
from app.event_types import ALL_EVENT_TYPES
from app.planner.commands import AddPlan, PlannerCommandHandler
from app.planner.events import Frequency, Repeat
from app.planner.projections import AgendaProjection
from app.reading.commands import ReadingCommandHandler, StartSeries
from app.reading.events import Kind
from app.reading.projections import LibraryProjection
from app.watching.commands import AddShow, WatchingCommandHandler
from app.watching.events import WatchKind, WatchStatus
from app.watching.projections import WatchlistProjection

TODAY = dt.date(2026, 10, 11)


@pytest.fixture
def data():
    """A small tracker with real projections and handlers on a :memory: store."""
    store = EventStore(":memory:", ALL_EVENT_TYPES)
    library, watchlist = LibraryProjection(), WatchlistProjection()
    listen, artists, tracks, agenda = (ListeningActivityProjection(), TopArtistsProjection(), TopTracksProjection(),
                                       AgendaProjection())
    for projection in (library, watchlist, listen, artists, tracks, agenda):
        store.subscribe(projection.apply)
    reading, watching, planner = ReadingCommandHandler(store, library), WatchingCommandHandler(store, watchlist), \
        PlannerCommandHandler(store)
    reading.handle(StartSeries("Solo Leveling", Kind.MANHWA, "asura", 119))
    reading.handle(StartSeries("Lord of the Mysteries", Kind.NOVEL, "", 400))
    watching.handle(AddShow("Severance", WatchKind.SERIES))
    planner.handle(AddPlan("Gym", dt.date(2026, 10, 12), dt.time(12), repeat=Repeat(Frequency.WEEKLY, (0,))))
    ListeningCommandHandler(store).handle(RecordPlay(dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc),
                                                     "spotify:track:x", "Midnight City", ["M83"], "Hurry Up", "a", 240_000))
    tracker = TrackerData(library=library, watchlist=watchlist, listening=listen,
                          top_artists=artists, top_tracks=tracks, agenda=agenda, reading=reading, watching=watching,
                          planner=planner, now=lambda: dt.datetime(2026, 10, 11, 10, 0))
    tracker.store = store
    return tracker


def series_id(data, title):
    return data.library.find_by_title(title).series_id


# ---- Prompt ----

def test_prompt_is_identity_soul_and_user_with_the_date_after_the_cached_part():
    blocks = system_blocks(TODAY)
    fixed, dated = blocks
    assert fixed["cache_control"] == {"type": "ephemeral"} and "cache_control" not in dated
    assert fixed["text"].index("Sunny: identity") < fixed["text"].index("Sunny: soul") < fixed["text"].index("Sunny: user")
    assert "Cannot lie" in fixed["text"] and "Call me **Progen**" in fixed["text"]
    assert "Sunday 11 October 2026" in dated["text"]
    assert system_blocks(dt.date(2026, 10, 12))[0] == fixed  # the fixed part stays byte-identical: cacheable


def test_tools_still_missing_is_gone_now_they_are_built():
    assert "Tools still missing" not in (IDENTITY_DIR / "IDENTITY.md").read_text(encoding="utf-8")
    assert "Tools still missing" not in system_blocks(TODAY)[0]["text"]


# ---- Tools ----

def test_tools_are_strict_and_the_last_one_carries_the_cache_marker():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert names == ["find_item", "reading_overview", "watching_overview", "top_tracks", "top_artists",
                     "listening_minutes", "plans_between", "set_reading_progress", "set_reading_status", "add_series",
                     "set_watch_status", "add_show", "add_plan", "plan_done", "plan_skip", "plan_reschedule", "plan_edit",
                     "delete_item"]
    for definition in TOOL_DEFINITIONS:
        schema = definition["input_schema"]
        assert definition["strict"] is True and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    assert TOOL_DEFINITIONS[-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in t for t in TOOL_DEFINITIONS[:-1])


def test_read_tools_use_the_projections_and_return_compact_results(data):
    result, pending = run_tool("reading_overview", {"status": "reading"}, data)
    assert pending is None
    assert {r["title"] for r in result} == {"Solo Leveling", "Lord of the Mysteries"}
    assert set(result[0]) == {"id", "title", "status", "chapter", "last_update", "days_ago"}  # only what is needed
    found, _ = run_tool("find_item", {"query": "solo levelling", "domain": None}, data)
    assert found[0]["title"] == "Solo Leveling" and found[0]["domain"] == "reading"
    tracks, _ = run_tool("top_tracks", {"from": "2026-09-01", "to": "2026-10-15", "limit": 5}, data)
    assert tracks == [{"track": "Midnight City", "artists": ["M83"], "plays": 1, "minutes": 4.0}]
    artists, _ = run_tool("top_artists", {"from": "2026-10-01", "to": "2026-10-01", "limit": 5}, data)
    assert artists == [{"artist": "M83", "plays": 1}]
    minutes, _ = run_tool("listening_minutes", {"from": "2026-10-01", "to": "2026-10-31"}, data)
    assert minutes == {"minutes": 4, "days_listened": 1}
    plans, _ = run_tool("plans_between", {"from": "2026-10-12", "to": "2026-10-20"}, data)
    assert [(p["title"], p["on"], p["time"], p["repeats"]) for p in plans] == [
        ("Gym", "2026-10-12", "12:00", True), ("Gym", "2026-10-19", "12:00", True)]
    shows, _ = run_tool("watching_overview", {"status": None}, data)
    assert shows[0]["title"] == "Severance" and set(shows[0]) == {"id", "title", "kind", "status"}


def test_read_tools_reject_bad_input(data):
    with pytest.raises(ToolError):
        run_tool("top_tracks", {"from": "1 sep", "to": "2026-10-15", "limit": 5}, data)
    with pytest.raises(ToolError):
        run_tool("set_reading_progress", {"series_id": "nope", "chapter": 3}, data)


def test_write_tools_only_propose_and_store_nothing(data):
    before = data.store.count()
    _, pending = run_tool("set_reading_progress", {"series_id": series_id(data, "Solo Leveling"), "chapter": 120}, data)
    assert pending.label == "Solo Leveling · chapter 119 → 120" and pending.changed == "reading-changed"
    _, plan = run_tool("add_plan", {"text": "elke ma 12:00 gym"}, data)
    assert plan.label == "New plan · gym · from Mon 12 Oct · 12:00 · every Mon"
    _, show = run_tool("set_watch_status", {"show_id": data.watchlist.all()[0].show_id, "status": "rewatching"}, data)
    assert show.label == "Severance · Watching → Rewatching"
    assert data.store.count() == before


def test_delete_proposals_carry_the_name(data):
    _, pending = run_tool("delete_item", {"domain": "reading", "id": series_id(data, "Solo Leveling")}, data)
    assert pending.label == "Delete · Solo Leveling" and pending.delete_name == "Solo Leveling"


# ---- The loop ----

def test_a_read_tool_round_then_the_answer(data):
    client = FakeClient(
        response(thinking(), tool("find_item", {"query": "solo", "domain": "reading"})),
        response(text("Solo Leveling, chapter 119. Last touched today.")),
    )
    reply = Agent(client, data).ask("Where am I in Solo Leveling?", [])
    assert reply.text.startswith("Solo Leveling, chapter 119") and reply.actions == []
    first, second = client.calls
    assert first["model"] == "claude-haiku-5-5"  # without a setting: the default model
    assert first["output_config"] == {"effort": agent_module.EFFORT} and agent_module.EFFORT == "low"
    assert first["max_tokens"] == 1024 and first["tools"] == TOOL_DEFINITIONS
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    [result] = second["messages"][-1]["content"]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_find_item"
    assert json.loads(result["content"])[0]["title"] == "Solo Leveling"


def test_a_write_tool_becomes_a_pending_action(data):
    sid = series_id(data, "Solo Leveling")
    client = FakeClient(response(tool("set_reading_progress", {"series_id": sid, "chapter": 120})),
                        response(text("Chapter 120 it is. Confirm and it's done.")))
    before = data.store.count()
    reply = Agent(client, data).ask("Read 120 of Solo Leveling", [])
    assert [a.label for a in reply.actions] == ["Solo Leveling · chapter 119 → 120"]
    assert client.calls[1]["messages"][-1]["content"][0]["content"] == "proposed, waiting for Progen's confirmation"
    assert data.store.count() == before  # nothing saved until Confirm


def test_a_bad_tool_call_goes_back_as_an_error(data):
    client = FakeClient(response(tool("set_reading_progress", {"series_id": "nope", "chapter": 3})),
                        response(text("Which one?")))
    Agent(client, data).ask("Read 3", [])
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "find_item" in result["content"]


def test_at_most_five_rounds(data):
    client = FakeClient(*[response(tool("reading_overview", {"status": None}, f"t{i}")) for i in range(5)])
    reply = Agent(client, data).ask("Loop forever", [])
    assert len(client.calls) == 5 and reply.text


def test_history_is_only_the_last_ten_messages_and_text_only():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(15)]
    trimmed = trim_history(history)
    assert len(trimmed) <= 10 and trimmed[0]["role"] == "user" and trimmed[-1]["content"] == "m14"
    assert all(isinstance(m["content"], str) for m in trimmed)


def test_an_api_error_is_a_friendly_reply(data):
    import anthropic
    import httpx2

    class Broken:
        def __init__(self):
            self.messages = self

        def create(self, **request):
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    reply = Agent(Broken(), data).ask("hi", [])
    assert reply.error and "reach" in reply.text


def test_no_proposal_that_changes_nothing(data):
    with pytest.raises(ToolError, match="already at chapter 119"):
        run_tool("set_reading_progress", {"series_id": series_id(data, "Solo Leveling"), "chapter": 119}, data)
