"""Fitness through the JSON ingest endpoints, as pages, on the home screen, in Settings and in Wrapped.
Fixed clock, no network."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import app.clock as clock
from app.main import create_app

NOW = dt.datetime(2026, 10, 17, 12, 0)  # a Saturday; the week began on Monday 12 October
HTMX = {"HX-Request": "true"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: NOW)
    return TestClient(create_app(":memory:"), headers={"Content-Type": "application/json"})


def send_steps(client, *pairs):
    return client.post("/fitness/ingest/steps", json={"days": [{"day": d, "steps": n} for d, n in pairs]})


def send_gym(client, event, at):
    return send_gym_lines(client, (event, at))


def send_gym_lines(client, *lines):
    """The lines of gym.txt as the Shortcut sends them: (event, at) pairs."""
    return client.post("/fitness/ingest/gym", json={"events": [{"event": e, "at": at} for e, at in lines]})


# ---- Ingest: steps ----

def test_steps_are_recorded_and_a_second_send_changes_nothing(client):
    first = send_steps(client, ("2026-10-15", 8123), ("2026-10-16", 4000))
    assert first.status_code == 200 and first.json() == {"recorded": 2, "unchanged": 0}
    assert send_steps(client, ("2026-10-15", 8123), ("2026-10-16", 4000)).json() == {"recorded": 0, "unchanged": 2}
    assert client.get("/fitness/steps").json() == [{"day": "2026-10-15", "steps": 8123},
                                                   {"day": "2026-10-16", "steps": 4000}]


def test_a_changed_number_is_recorded_again_and_the_last_one_counts(client):
    send_steps(client, ("2026-10-16", 4000), ("2026-10-15", 8123))
    assert send_steps(client, ("2026-10-16", 6500), ("2026-10-15", 8123)).json() == {"recorded": 1, "unchanged": 1}
    assert {s["day"]: s["steps"] for s in client.get("/fitness/steps").json()}["2026-10-16"] == 6500


def test_steps_can_come_as_text_the_way_a_shortcut_sends_them(client):
    assert send_steps(client, ("2026-10-16", "7000")).json() == {"recorded": 1, "unchanged": 0}


@pytest.mark.parametrize("steps", [-1, 200_001])
def test_impossible_steps_are_refused_with_a_message_and_nothing_is_saved(client, steps):
    response = send_steps(client, ("2026-10-15", 8000), ("2026-10-16", steps))
    assert response.status_code == 400 and "between 0 and 200,000" in response.json()["detail"]
    assert client.get("/fitness/steps").json() == []  # the good day was not saved either


def test_at_most_31_days_per_request_and_at_least_one(client):
    start = dt.date(2026, 9, 1)
    days = [((start + dt.timedelta(days=i)).isoformat(), 1000) for i in range(32)]
    assert send_steps(client, *days).status_code == 422
    assert send_steps(client, *days[:31]).json() == {"recorded": 31, "unchanged": 0}
    assert send_steps(client).status_code == 422


def test_a_bad_date_is_refused(client):
    assert send_steps(client, ("yesterday", 100)).status_code == 422


# ---- Ingest: gym ----

def test_arriving_and_leaving_make_a_visit(client):
    response = send_gym_lines(client, ("arrived", "2026-10-17T10:00:00"), ("left", "2026-10-17T11:15:00"))
    assert response.status_code == 200 and response.json() == {"recorded": 2, "unchanged": 0}
    [visit] = client.get("/fitness/visits").json()
    assert (visit["day"], visit["start"], visit["end"], visit["minutes"]) == (
        "2026-10-17", "2026-10-17T10:00:00", "2026-10-17T11:15:00", 75)


def test_the_same_message_twice_makes_one_event(client):
    lines = [("arrived", "2026-10-17T10:00:00"), ("left", "2026-10-17T11:00:00")]
    assert send_gym_lines(client, *lines).json() == {"recorded": 2, "unchanged": 0}
    assert send_gym_lines(client, *lines).json() == {"recorded": 0, "unchanged": 2}
    assert send_gym_lines(client, ("arrived", "2026-10-17T10:00:40")).json() == {"recorded": 0, "unchanged": 1}
    assert len(client.get("/fitness/visits").json()) == 1


def test_the_same_message_twice_in_one_request_makes_one_event(client):
    lines = [("arrived", "2026-10-17T10:00:00")] * 2
    assert send_gym_lines(client, *lines).json() == {"recorded": 1, "unchanged": 1}


def test_a_departure_that_comes_in_before_its_arrival_is_still_one_visit(client):
    assert send_gym_lines(client, ("left", "2026-10-17T11:30:00"), ("arrived", "2026-10-17T10:00:00")
                          ).json() == {"recorded": 2, "unchanged": 0}
    [visit] = client.get("/fitness/visits").json()
    assert visit["minutes"] == 90


def test_the_last_30_lines_of_gym_txt_come_in_again_and_late(client):
    old = [("arrived", "2026-10-14T18:00:00"), ("left", "2026-10-14T19:00:00")]
    assert send_gym_lines(client, *old).json()["recorded"] == 2
    again = [*old, ("left", "2026-10-15T19:45:00"), ("arrived", "2026-10-15T18:30:00")]  # two new ones, any order
    assert send_gym_lines(client, *again).json() == {"recorded": 2, "unchanged": 2}
    assert [(v["day"], v["minutes"]) for v in client.get("/fitness/visits").json()] == [
        ("2026-10-14", 60), ("2026-10-15", 75)]  # messages from an earlier day land on that day


def test_a_departure_without_an_arrival_is_saved_but_makes_no_visit(client):
    assert send_gym_lines(client, ("left", "2026-10-17T11:00:00")).json()["recorded"] == 1
    assert client.get("/fitness/visits").json() == []


def test_a_time_with_an_offset_becomes_local_time(client):
    send_gym(client, "arrived", "2026-10-17T07:30:00+02:00")
    expected = dt.datetime.fromisoformat("2026-10-17T07:30:00+02:00").astimezone().replace(tzinfo=None)
    assert send_gym(client, "left", "2026-10-17T05:50:00Z").json()["recorded"] == 1  # Z is also an offset
    [visit] = client.get("/fitness/visits").json()
    assert visit["start"] == expected.isoformat() and visit["minutes"] == 20


def test_at_is_required_and_there_is_at_least_one_and_at_most_50_lines(client):
    assert client.post("/fitness/ingest/gym", json={"events": [{"event": "arrived"}]}).status_code == 422
    assert client.post("/fitness/ingest/gym", json={"events": []}).status_code == 422
    assert client.post("/fitness/ingest/gym", json={"event": "arrived", "at": "2026-10-17T10:00:00"}).status_code == 422
    start = dt.datetime(2026, 10, 1, 8)
    lines = [("arrived", (start + dt.timedelta(hours=i * 6)).isoformat()) for i in range(51)]
    assert send_gym_lines(client, *lines).status_code == 422
    assert send_gym_lines(client, *lines[:50]).json() == {"recorded": 50, "unchanged": 0}


def test_an_unknown_event_is_refused(client):
    assert send_gym(client, "visited", "2026-10-17T10:00:00").status_code == 422


def test_a_time_in_the_future_is_refused_and_nothing_of_the_request_is_saved(client):
    response = send_gym_lines(client, ("arrived", "2026-10-17T10:00:00"), ("arrived", "2026-10-18T10:00:00"))
    assert response.status_code == 400 and "future" in response.json()["detail"]
    assert client.get("/fitness/visits", params={"days": 5}).json() == []


def test_the_csrf_rule_still_holds_for_the_ingest(client):
    plain = client.post("/fitness/ingest/gym", content='{"events": []}', headers={"Content-Type": "text/plain"})
    assert plain.status_code == 403


# ---- Pages ----

def test_the_empty_pages_explain_where_the_data_comes_from(client):
    dashboard = client.get("/ui/fitness/dashboard", headers=HTMX).text
    assert "Nothing here yet" in dashboard and "Shortcuts" in dashboard
    assert "Nothing yet" in client.get("/ui/fitness/history", headers=HTMX).text


def test_the_dashboard_shows_this_week(client):
    send_steps(client, ("2026-10-12", 4000), ("2026-10-13", 8000), ("2026-10-09", 20000))
    send_gym(client, "arrived", "2026-10-13T18:00:00")
    send_gym(client, "left", "2026-10-13T19:30:00")
    page = client.get("/ui/fitness/dashboard", headers=HTMX).text
    assert "1 gym visit this week" in page
    assert '<div class="stat-value">1.5</div>' in page          # gym hours
    assert '<div class="stat-value">6000</div>' in page         # average steps of Monday and Tuesday
    assert "Steps per day" in page and "Gym visits per week" in page
    assert "Tue 13 Oct" in page and "18:00–19:30 · 1 h 30 min" in page
    assert page.count('class="bar-fill"') == 14 + 8
    assert '<span class="bar-value">8k</span>' in page and 'Tue 13 Oct: 8,000 steps' in page


def test_the_history_lists_the_days_newest_first(client):
    send_steps(client, ("2026-10-15", 8123), ("2026-10-16", 4000))
    send_gym(client, "arrived", "2026-10-16T18:00:00")
    send_gym(client, "left", "2026-10-16T18:45:00")
    send_gym(client, "arrived", "2026-10-17T09:00:00")  # still there, 3 hours ago
    page = client.get("/ui/fitness/history", headers=HTMX).text
    assert page.index("Sat 17 Oct") < page.index("Fri 16 Oct") < page.index("Thu 15 Oct")
    assert "4000 steps · gym 18:00–18:45 (45 min)" in page and "gym 09:00 (now)" in page


def test_a_visit_without_a_departure_after_four_hours_shows_unknown(client):
    send_gym(client, "arrived", "2026-10-17T06:00:00")
    assert "gym 06:00 (unknown)" in client.get("/ui/fitness/history", headers=HTMX).text


def test_fitness_is_on_the_home_screen_and_in_settings(client):
    send_gym(client, "arrived", "2026-10-17T10:00:00")
    home = client.get("/ui/home", headers=HTMX).text
    assert "Fitness" in home and "At the gym since 10:00" in home and "avg steps" in home
    settings = client.get("/ui/settings", headers=HTMX).text
    assert "Fitness" in settings and "gym visits" in settings


def test_the_shell_has_the_fitness_link(client):
    assert 'data-page="fitness"' in client.get("/").text
    assert "fitness: { add: null" in client.get("/static/trackly.js").text


def test_steps_above_the_bars_are_short_and_the_tooltip_has_separators(client):
    send_steps(client, ("2026-10-15", 8123), ("2026-10-16", 12_000), ("2026-10-14", 640))
    page = client.get("/ui/fitness/dashboard", headers=HTMX).text
    assert '<span class="bar-value">8.1k</span>' in page and '<span class="bar-value">12k</span>' in page
    assert '<span class="bar-value">640</span>' in page
    assert "Thu 15 Oct: 8,123 steps" in page and "Fri 16 Oct: 12,000 steps" in page


# ---- Wrapped ----

def test_wrapped_counts_fitness(client):
    send_steps(client, ("2026-10-12", 4000), ("2026-10-13", 9500))
    send_gym(client, "arrived", "2026-10-13T18:00:00")
    send_gym(client, "left", "2026-10-13T19:30:00")
    send_gym(client, "arrived", "2026-10-14T18:00:00")
    send_gym(client, "left", "2026-10-14T18:05:00")  # walking past: doesn't count
    page = client.get("/ui/wrapped", params={"year": 2026}, headers=HTMX).text
    assert "<b>1</b><span>gym visits</span>" in page and "<b>1.5</b><span>gym hours</span>" in page
    assert "<b>13,500</b><span>steps</span>" in page and "Tue 13 Oct · 9500 steps" in page
    assert "No gym visits or steps in 2025" in client.get("/ui/wrapped", params={"year": 2025}, headers=HTMX).text
