"""Sunny: prompt, tools and the agent loop. With a fake client, never the real API."""
import datetime as dt
import json

import pytest
from fakes import FakeClient, response, text, thinking, tool

from app.assistant import agent as agent_module
from app.assistant.agent import Agent, trim_history
from app.assistant.prompt import IDENTITY_DIR, system_blocks
from app.assistant.tools import (
    MAX_STRICT_TOOLS, READ_TOOLS, TOOL_DEFINITIONS, WRITE_TOOLS, ToolError, TrackerData, run_tool,
)
from app.eventstore import EventStore
from app.listening.commands import ListeningCommandHandler, RecordPlay
from app.listening.projections import ListeningActivityProjection, TopArtistsProjection, TopTracksProjection
from app.event_types import ALL_EVENT_TYPES
from app.planner.commands import AddPlan, PlannerCommandHandler
from app.planner.events import Frequency, Repeat
from app.planner.projections import AgendaProjection
from app.reading.commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, StartSeries
from app.reading.events import Kind, Status
from app.reading.projections import LibraryProjection, ReadingActivityProjection, ReadingHistoryProjection
from app.watching.commands import AddShow, WatchingCommandHandler
from app.watching.events import WatchKind, WatchStatus
from app.watching.projections import WatchHistoryProjection, WatchlistProjection

TODAY = dt.date(2026, 10, 11)


@pytest.fixture
def data():
    """A small tracker with real projections and handlers on a :memory: store."""
    store = EventStore(":memory:", ALL_EVENT_TYPES)
    library, watchlist = LibraryProjection(), WatchlistProjection()
    listen, artists, tracks, agenda = (ListeningActivityProjection(), TopArtistsProjection(), TopTracksProjection(),
                                       AgendaProjection())
    reading_activity, reading_history, watch_history = (ReadingActivityProjection(), ReadingHistoryProjection(),
                                                        WatchHistoryProjection())
    for projection in (library, watchlist, listen, artists, tracks, agenda, reading_activity, reading_history,
                       watch_history):
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
                          top_artists=artists, top_tracks=tracks, agenda=agenda, reading_activity=reading_activity,
                          reading_history=reading_history,
                          watch_history=watch_history, reading=reading, watching=watching,
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
    assert names == ["find_item", "library", "listening_stats", "plans_between", "history_between", "notices",
                     "item_history", "set_reading_progress", "set_status", "add_item", "add_plan", "plan_done",
                     "plan_skip", "plan_reschedule", "plan_edit", "delete_item"]
    assert names == list(READ_TOOLS) + list(WRITE_TOOLS)
    strict = {t["name"]: t["strict"] for t in TOOL_DEFINITIONS}
    assert all(strict[name] for name in WRITE_TOOLS) and not any(strict[name] for name in READ_TOOLS)
    for definition in TOOL_DEFINITIONS:
        schema = definition["input_schema"]
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    # The API refuses more than 20 strict tools with a 400, so every message would fail.
    assert sum(strict.values()) <= MAX_STRICT_TOOLS == 20
    assert TOOL_DEFINITIONS[-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in t for t in TOOL_DEFINITIONS[:-1])


def test_read_tools_use_the_projections_and_return_compact_results(data):
    result, pending = run_tool("library", {"domain": "reading", "status": "reading"}, data)
    assert pending is None
    assert {r["title"] for r in result} == {"Solo Leveling", "Lord of the Mysteries"}
    assert set(result[0]) == {"id", "title", "status", "chapter", "last_update", "days_ago"}  # only what is needed
    found, _ = run_tool("find_item", {"query": "solo levelling", "domain": None}, data)
    assert found[0]["title"] == "Solo Leveling" and found[0]["domain"] == "reading"
    stats, _ = run_tool("listening_stats", {"from": "2026-09-01", "to": "2026-10-15", "limit": 5}, data)
    assert stats["top_tracks"] == [{"track": "Midnight City", "artists": ["M83"], "plays": 1, "minutes": 4.0}]
    day, _ = run_tool("listening_stats", {"from": "2026-10-01", "to": "2026-10-01", "limit": 5}, data)
    assert day["top_artists"] == [{"artist": "M83", "plays": 1}]
    month, _ = run_tool("listening_stats", {"from": "2026-10-01", "to": "2026-10-31", "limit": None}, data)
    assert (month["minutes"], month["days_listened"]) == (4, 1)
    plans, _ = run_tool("plans_between", {"from": "2026-10-12", "to": "2026-10-20"}, data)
    assert [(p["title"], p["on"], p["time"], p["repeats"]) for p in plans] == [
        ("Gym", "2026-10-12", "12:00", True), ("Gym", "2026-10-19", "12:00", True)]
    shows, _ = run_tool("library", {"domain": "watching", "status": None}, data)
    assert shows[0]["title"] == "Severance" and set(shows[0]) == {"id", "title", "kind", "status"}


def test_read_tools_reject_bad_input(data):
    with pytest.raises(ToolError):
        run_tool("listening_stats", {"from": "1 sep", "to": "2026-10-15", "limit": 5}, data)
    with pytest.raises(ToolError):
        run_tool("set_reading_progress", {"series_id": "nope", "chapter": 3}, data)


def test_write_tools_only_propose_and_store_nothing(data):
    before = data.store.count()
    _, pending = run_tool("set_reading_progress", {"series_id": series_id(data, "Solo Leveling"), "chapter": 120}, data)
    assert pending.label == "Solo Leveling · chapter 119 → 120" and pending.changed == "reading-changed"
    _, plan = run_tool("add_plan", {"text": "elke ma 12:00 gym"}, data)
    assert plan.label == "New plan · gym · from Mon 12 Oct · 12:00 · every Mon"
    _, show = run_tool("set_status", {"domain": "watching", "id": data.watchlist.all()[0].show_id,
                                      "status": "rewatching"}, data)
    assert show.label == "Severance · Watching → Rewatching"
    assert data.store.count() == before


def test_delete_proposals_carry_the_name(data):
    _, pending = run_tool("delete_item", {"domain": "reading", "id": series_id(data, "Solo Leveling")}, data)
    assert pending.label == "Delete · Solo Leveling" and pending.delete_name == "Solo Leveling"


# ---- Merged tools ----

def test_listening_stats_gives_minutes_days_and_both_top_lists(data):
    stats, _ = run_tool("listening_stats", {"from": "2026-10-01", "to": "2026-10-31", "limit": 5}, data)
    assert stats == {"minutes": 4, "days_listened": 1, "top_artists": [{"artist": "M83", "plays": 1}],
                     "top_tracks": [{"track": "Midnight City", "artists": ["M83"], "plays": 1, "minutes": 4.0}]}
    empty, _ = run_tool("listening_stats", {"from": "2026-09-01", "to": "2026-09-30", "limit": 5}, data)
    assert empty == {"minutes": 0, "days_listened": 0, "top_artists": [], "top_tracks": []}
    with pytest.raises(ToolError, match="date"):
        run_tool("listening_stats", {"from": "2026-10-01", "to": "end of october", "limit": 5}, data)


def test_library_gives_each_domain_its_rows_and_checks_the_status(data):
    reading, _ = run_tool("library", {"domain": "reading", "status": None}, data)
    assert {r["title"] for r in reading} == {"Solo Leveling", "Lord of the Mysteries"} and "chapter" in reading[0]
    watching, _ = run_tool("library", {"domain": "watching", "status": "watching"}, data)
    assert watching == [{"id": data.watchlist.all()[0].show_id, "title": "Severance", "kind": "series",
                         "status": "watching"}]
    with pytest.raises(ToolError, match="'rewatching' is not a reading status. Use one of: reading, on_hold"):
        run_tool("library", {"domain": "reading", "status": "rewatching"}, data)
    with pytest.raises(ToolError, match="not a watching status"):
        run_tool("library", {"domain": "watching", "status": "plan_to_read"}, data)


def test_set_status_proposes_the_same_cards_for_reading_and_watching(data):
    _, reading = run_tool("set_status", {"domain": "reading", "id": series_id(data, "Solo Leveling"),
                                         "status": "completed"}, data)
    assert (reading.domain, reading.label, reading.changed) == (
        "reading", "Solo Leveling · Reading → Completed", "reading-changed")
    show = data.watchlist.all()[0].show_id
    _, watching = run_tool("set_status", {"domain": "watching", "id": show, "status": "on_hold"}, data)
    assert (watching.domain, watching.label, watching.changed) == (
        "watching", "Severance · Watching → On hold", "watching-changed")
    with pytest.raises(ToolError, match="'rewatching' is not a reading status"):
        run_tool("set_status", {"domain": "reading", "id": series_id(data, "Solo Leveling"), "status": "rewatching"},
                 data)
    with pytest.raises(ToolError, match="already watching"):
        run_tool("set_status", {"domain": "watching", "id": show, "status": "watching"}, data)
    with pytest.raises(ToolError, match="find_item"):
        run_tool("set_status", {"domain": "watching", "id": series_id(data, "Solo Leveling"), "status": "dropped"},
                 data)


def test_add_item_picks_the_domain_from_the_kind(data):
    _, manhwa = run_tool("add_item", {"title": "Tower of God", "kind": "manhwa", "status": None}, data)
    assert (manhwa.domain, manhwa.label, manhwa.command.status) == ("reading", "New manhwa · Tower of God",
                                                                    Status.READING)
    _, anime = run_tool("add_item", {"title": "Frieren", "kind": "anime", "status": None}, data)
    assert (anime.domain, anime.label, anime.command.status) == ("watching", "New anime · Frieren (Watching)",
                                                                 WatchStatus.WATCHING)
    _, backlog = run_tool("add_item", {"title": "Omniscient Reader", "kind": "novel", "status": "plan_to_read"}, data)
    assert backlog.command.status is Status.PLAN_TO_READ and backlog.label == "New novel · Omniscient Reader (Plan to read)"
    with pytest.raises(ToolError, match="already in the library"):
        run_tool("add_item", {"title": " solo leveling ", "kind": "manhwa", "status": None}, data)
    with pytest.raises(ToolError, match="already in the list"):
        run_tool("add_item", {"title": "Severance", "kind": "series", "status": None}, data)
    with pytest.raises(ToolError, match="not a watching status"):
        run_tool("add_item", {"title": "Dune", "kind": "movie", "status": "plan_to_read"}, data)


def test_read_tools_work_without_the_nullable_fields(data):
    assert run_tool("library", {"domain": "watching"}, data)[0][0]["title"] == "Severance"
    assert run_tool("listening_stats", {"from": "2026-10-01", "to": "2026-10-31"}, data)[0]["minutes"] == 4
    assert run_tool("find_item", {"query": "severance"}, data)[0][0]["title"] == "Severance"
    assert "reading" in run_tool("history_between", {"from": "2026-10-01", "to": "2026-10-31"}, data)[0]
    with pytest.raises(ToolError, match="domain"):
        run_tool("library", {}, data)


# ---- History tools ----

def test_history_between_is_compact_json_for_reading_and_watching(data):
    solo = series_id(data, "Solo Leveling")
    data.reading.handle(LogProgress(solo, 125))
    data.reading.handle(ChangeStatus(solo, Status.COMPLETED))
    today = dt.date.today().isoformat()  # the events carry the real time
    result, pending = run_tool("history_between", {"from": today, "to": today, "domain": None}, data)
    assert pending is None
    assert result["reading"] == [{"title": "Solo Leveling", "kind": "manhwa", "chapters": 6, "from_chapter": 119,
                                  "to_chapter": 125, "changes": [f"completed {today}"]}]
    assert result["watching"] == [{"title": "Severance", "kind": "series", "finished_on": None, "rewatch": False}]
    assert result["totals"] == {"finished": 1, "chapters": 6, "days_read": 1}
    json.dumps(result)
    only, _ = run_tool("history_between", {"from": today, "to": today, "domain": "watching"}, data)
    assert set(only) == {"watching", "totals"}


def test_item_history_includes_earlier_removed_versions(data):
    old = series_id(data, "Solo Leveling")
    data.reading.handle(LogProgress(old, 130))
    data.reading.handle(RemoveSeries(old))
    data.reading.handle(StartSeries("Solo Leveling", Kind.MANHWA, "", 130))
    result, _ = run_tool("item_history", {"domain": "reading", "id": series_id(data, "Solo Leveling")}, data)
    assert result["chapters"] == 11 and result["start_status"] == "reading" and result["changes"] == []
    assert result["first_read"] == result["last_read"] == dt.date.today().isoformat()
    show = data.watchlist.all()[0].show_id
    watched, _ = run_tool("item_history", {"domain": "watching", "id": show}, data)
    assert watched == {"title": "Severance", "kind": "series", "added": dt.date.today().isoformat(),
                       "start_status": "watching", "changes": [], "times_completed": 0, "completed_on": [],
                       "times_rewatched": 0}


def test_history_tools_reject_an_unknown_id_or_a_bad_date(data):
    with pytest.raises(ToolError, match="find_item"):
        run_tool("item_history", {"domain": "reading", "id": "nope"}, data)
    with pytest.raises(ToolError, match="find_item"):
        run_tool("item_history", {"domain": "watching", "id": "nope"}, data)
    with pytest.raises(ToolError, match="date"):
        run_tool("history_between", {"from": "september", "to": "2026-09-30", "domain": None}, data)
    with pytest.raises(ToolError):
        run_tool("history_between", {"from": "2026-09-30", "to": "2026-09-01", "domain": "reading"}, data)


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
    client = FakeClient(*[response(tool("library", {"domain": "reading", "status": None}, f"t{i}")) for i in range(5)])
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

        def stream(self, **request):
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    reply = Agent(Broken(), data).ask("hi", [])
    assert reply.error and "reach" in reply.text


def test_no_proposal_that_changes_nothing(data):
    with pytest.raises(ToolError, match="already at chapter 119"):
        run_tool("set_reading_progress", {"series_id": series_id(data, "Solo Leveling"), "chapter": 119}, data)
