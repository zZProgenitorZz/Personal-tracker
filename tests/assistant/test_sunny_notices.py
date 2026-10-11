"""Sunny's notices: fixed lines from the projections, no AI and no tokens. With a fixed clock."""
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.clock as clock
from app.assistant.notices import notices, visible
from app.assistant.tools import run_tool
from app.main import create_app
from app.planner.commands import AddPlan
from app.planner.events import PlanAdded, PlanDone
from app.planner.projections import AgendaProjection
from app.reading.events import Kind, ProgressLogged, SeriesStarted, Status, StatusChanged
from app.reading.projections import LibraryProjection, ReadingActivityProjection
from app.watching.events import ShowAdded, WatchKind, WatchStatus
from app.watching.projections import WatchlistProjection

NOW = dt.datetime(2026, 10, 11, 10, 0)  # local time, like the planner


def ago(days=0, hours=0) -> dt.datetime:
    """A moment `days` before NOW, in the local time zone like real events."""
    return (NOW - dt.timedelta(days=days, hours=hours)).astimezone()


def tracker(*events):
    data = SimpleNamespace(library=LibraryProjection(), watchlist=WatchlistProjection(), agenda=AgendaProjection(),
                           reading_activity=ReadingActivityProjection(), dismissed=set())
    for event in events:
        for projection in (data.library, data.watchlist, data.agenda, data.reading_activity):
            projection.apply(event)
    return data


def kinds(data, now=NOW):
    return [(n.kind, n.text) for n in notices(data, now)]


def series(sid, title, days_ago):
    return SeriesStarted(sid, title, Kind.MANHWA, "x", 1, at=ago(days_ago))


# ---- notices() ----

def test_a_series_quiet_for_25_days_gathers_dust_and_one_for_10_days_does_not():
    data = tracker(series("old", "Tower of God", 25), series("new", "Omniscient Reader", 10))
    found = notices(data, NOW)
    assert [(n.kind, n.key) for n in found] == [("dusty_reading", "dusty:reading:old")]
    assert found[0].text == "Tower of God has been gathering dust for 25 days."
    assert [(a.label, a.url, a.vals) for a in found[0].actions] == [
        ("On hold", "/ui/reading/series/old/status", {"status": "on_hold"}),
        ("Dropped", "/ui/reading/series/old/status", {"status": "dropped"})]
    assert found[0].actions[1].confirm == "Tower of God"  # the same confirmation as elsewhere


def test_at_most_three_dusty_items_oldest_first():
    data = tracker(*[series(str(n), f"Series {n}", 22 + n) for n in range(5)],
                   *[ShowAdded(f"s{n}", f"Show {n}", WatchKind.SERIES, WatchStatus.WATCHING, at=ago(40 + n))
                     for n in range(4)])
    found = notices(data, NOW)
    assert [n.text.split(" has")[0] for n in found if n.kind == "dusty_reading"] == ["Series 4", "Series 3", "Series 2"]
    assert sum(n.kind == "dusty_watching" for n in found) == 3


def test_the_streak_notice_only_in_the_evening_and_only_without_chapters_today():
    reading = [series("1", "A", 10)] + [ProgressLogged("1", 10 + d, 9 + d, at=ago(d)) for d in (3, 2, 1)]
    evening = NOW.replace(hour=19)
    assert kinds(tracker(*reading), evening) == [("streak", "Your 3-day streak ends tonight. One chapter would do.")]
    assert kinds(tracker(*reading), NOW.replace(hour=17)) == []
    today = ProgressLogged("1", 14, 13, at=evening.replace(hour=8).astimezone())
    assert kinds(tracker(*reading, today), evening) == []


def test_a_plan_at_nine_shows_at_ten_until_it_is_done():
    plan = PlanAdded("p1", "Dentist", NOW.date(), dt.time(9), None, "", None, at=ago(days=2))
    assert kinds(tracker(plan), NOW.replace(hour=8)) == []
    found = notices(tracker(plan), NOW)
    assert [(n.kind, n.text) for n in found] == [("plans_today", "Dentist at 09:00 came and went. Done?")]
    assert [(a.label, a.url, a.vals) for a in found[0].actions] == [
        ("Done", "/ui/planner/plans/p1/done", {"on": "2026-10-11"})]
    assert kinds(tracker(plan, PlanDone("p1", NOW.date(), at=ago())), NOW) == []


def test_an_all_day_plan_counts_from_noon():
    plan = PlanAdded("p1", "Birthday Sam", NOW.date(), None, None, "", None, at=ago(days=2))
    assert kinds(tracker(plan), NOW) == []
    assert kinds(tracker(plan), NOW.replace(hour=13)) == [("plans_today", "Birthday Sam is today and still not ticked off.")]


def test_an_old_backlog_links_to_the_library_instead_of_deleting():
    data = tracker(series("b", "Lookism", 200),
                   StatusChanged("b", Status.READING, Status.PLAN_TO_READ, at=ago(200)),
                   ShowAdded("w", "Twin Peaks", WatchKind.SERIES, WatchStatus.PLAN_TO_WATCH, at=ago(130)),
                   ShowAdded("x", "Recent", WatchKind.MOVIE, WatchStatus.PLAN_TO_WATCH, at=ago(30)))
    found = [n for n in notices(data, NOW) if n.kind == "old_backlog"]
    assert [n.text for n in found] == ["Lookism has been on Plan to Read for 200 days. Still keen?",
                                       "Twin Peaks has been on Plan to Watch for 130 days. Still keen?"]
    assert [(a.label, a.method, a.url) for a in found[0].actions] == [("Remove from backlog", "link", "#reading/library")]


def test_not_today_hides_a_notice_until_the_next_day():
    data = tracker(series("old", "Tower of God", 25))
    data.dismissed.add(("dusty:reading:old", NOW.date()))
    assert visible(data, NOW) == []
    assert [n.key for n in visible(data, NOW + dt.timedelta(days=1))] == ["dusty:reading:old"]


# ---- In the app ----

@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: NOW)
    app = create_app(":memory:")
    app.test_client = TestClient(app, headers={"HX-Request": "true"})
    return app


def add_dentist(app):
    [added] = app.state.sunny.data.planner.handle(AddPlan("Dentist", NOW.date(), dt.time(9)))
    return added.plan_id


def test_the_notices_block_renders_the_actions_and_the_count(app):
    plan_id = add_dentist(app)
    page = app.test_client.get("/ui/assistant/notices").text
    assert "Dentist at 09:00 came and went. Done?" in page
    assert f'hx-post="/ui/planner/plans/{plan_id}/done"' in page and "2026-10-11" in page
    assert 'hx-post="/ui/assistant/notices/dismiss"' in page and "Not today" in page
    assert 'id="sunny-badge"' in page and 'data-count="1"' in page and "hidden" not in page.split('id="sunny-badge"')[1]


def test_with_nothing_to_say_the_block_is_empty_and_the_count_is_zero(app):
    page = app.test_client.get("/ui/assistant/notices").text
    assert "sunny-notice" not in page and 'data-count="0"' in page and "hidden" in page


def test_done_and_not_today_both_take_a_notice_away(app, monkeypatch):
    plan_id = add_dentist(app)
    client = app.test_client
    key = f"plan:{plan_id}:2026-10-11"
    assert 'data-count="0"' in client.post("/ui/assistant/notices/dismiss", data={"key": key}).text
    monkeypatch.setattr(clock, "local_now", lambda: NOW + dt.timedelta(days=1))
    assert "Dentist" not in client.get("/ui/assistant/notices").text  # tomorrow it isn't today's plan anymore
    monkeypatch.setattr(clock, "local_now", lambda: NOW)
    app.state.sunny.dismissed.clear()
    done = client.post(f"/ui/planner/plans/{plan_id}/done", data={"on": "2026-10-11"})
    assert done.headers["HX-Trigger"] == "planner-changed"  # the block refreshes on this
    assert 'data-count="0"' in client.get("/ui/assistant/notices").text


def test_the_notices_tool_returns_compact_json_without_the_dismissed_ones(app):
    add_dentist(app)
    data = app.state.sunny.data
    result, pending = run_tool("notices", {}, data)
    assert pending is None
    assert result == [{"kind": "plans_today", "text": "Dentist at 09:00 came and went. Done?"}]
    json.dumps(result)
    app.state.sunny.dismiss(notices(data, NOW)[0].key)
    assert run_tool("notices", {}, data)[0] == []


def test_sunny_has_a_pop_in_for_reminders_and_a_badge_on_the_button(app):
    page = app.test_client.get("/").text
    assert 'id="sunny-pop"' in page and 'id="sunny-pop-list"' in page and 'id="sunny-badge"' in page
    assert 'hx-get="/ui/assistant/notices"' in page and "planner-changed from:body" in page
