"""Planner als webpagina (#planner), op het startscherm, in Settings en als .ics. Vaste klok, geen netwerk."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import app.planner.schedule as schedule
from app.main import create_app

NOW = dt.datetime(2026, 10, 8, 10, 0)  # donderdag


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(schedule, "local_now", lambda: NOW)
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def add(client, text, **more):
    response = client.post("/ui/planner/plans", data={"text": text, **more})
    assert "toast-error" not in response.text, response.text
    return response


def plan_id(client, title):
    return next(p["plan_id"] for p in client.get("/planner/plans").json() if p["title"] == title)


# ---- Snel invoeren ----

def test_quick_add_parses_the_text_and_announces_the_change(client):
    response = add(client, "Tandarts vr 14u")
    assert response.headers["HX-Trigger"] == "planner-changed" and "Tandarts" in response.text
    [plan] = client.get("/planner/plans").json()
    assert (plan["title"], plan["day"], plan["time"]) == ("Tandarts", "2026-10-09", "14:00:00")


def test_preview_shows_what_the_app_makes_of_it(client):
    assert "Tandarts · Fri 9 Oct · 14:00" in client.get("/ui/planner/preview", params={"text": "Tandarts vr 14u"}).text
    assert client.get("/ui/planner/preview", params={"text": "  "}).text.strip() == ""


def test_empty_title_gives_an_error_toast(client):
    response = client.post("/ui/planner/plans", data={"text": "morgen 14u"})
    assert "toast-error" in response.text and "HX-Trigger" not in response.headers


def test_more_sets_note_duration_and_repeat(client):
    add(client, "Gym morgen 7:00", note="Legs", duration_min="45", repeat="weekly", weekdays=["0", "3"],
        until="2026-12-31")
    [plan] = client.get("/planner/plans").json()
    assert (plan["note"], plan["duration_min"]) == ("Legs", 45)
    assert plan["repeat"] == {"every": "weekly", "weekdays": [0, 3], "until": "2026-12-31"}


def test_more_can_turn_off_a_repeat_from_the_text(client):
    add(client, "Gym elke dag", repeat="none")
    assert client.get("/planner/plans").json()[0]["repeat"] is None


# ---- Week, Day, Someday ----

def test_week_shows_seven_days_with_today_and_plans(client):
    add(client, "Tandarts vr 14u")
    add(client, "Gym elke ma en do 18:00")
    page = client.get("/ui/planner/week").text
    assert page.count('class="week-day') == 7 and page.count("is-today") == 1
    assert "Tandarts" in page and "14:00" in page and 'data-repeat' in page
    assert 'href="#planner"' in page and 'href="#planner/day"' in page and 'href="#planner/someday"' in page


def test_week_navigation(client):
    page = client.get("/ui/planner/week", params={"start": "2026-10-12"}).text
    assert "12 – 18 Oct" in page and "start=2026-10-05" in page and "start=2026-10-19" in page
    assert client.get("/ui/planner/week", params={"start": "rubbish"}).status_code == 200


def test_day_is_a_timeline(client):
    add(client, "Verjaardag Sam vandaag")
    add(client, "Lunch vandaag 12:30", duration_min="60")
    page = client.get("/ui/planner/day").text
    assert page.index("Verjaardag Sam") < page.index("Lunch") and "12:30–13:30" in page and "All day" in page
    assert "Nothing planned" in client.get("/ui/planner/day", params={"d": "2026-10-20"}).text


def test_someday_lists_plans_without_a_date(client):
    add(client, "Learn Korean")
    page = client.get("/ui/planner/someday").text
    assert "Learn Korean" in page and "Plan it" in page
    assert "Learn Korean" not in client.get("/ui/planner/week").text


# ---- De dialog van een plan ----

def test_dialog_has_the_actions(client):
    add(client, "Tandarts vr 14u")
    pid = plan_id(client, "Tandarts")
    page = client.get(f"/ui/planner/plans/{pid}", params={"on": "2026-10-09"}).text
    assert "Mark done" in page and f'href="/ui/planner/{pid}.ics"' in page and "Send to iPhone" in page
    assert "Mail the file to yourself and open it on your iPhone." in page
    assert 'hx-confirm="Tandarts"' in page and "Skip this time" not in page


def test_dialog_of_a_repeat_can_skip(client):
    add(client, "Gym elke ma 7:00")
    page = client.get(f"/ui/planner/plans/{plan_id(client, 'Gym')}", params={"on": "2026-10-12"}).text
    assert "Skip this time" in page and "every Mon" in page


def test_done_reopen_skip_reschedule_edit_and_remove(client):
    add(client, "Gym elke ma 7:00")
    pid = plan_id(client, "Gym")
    for path, data in [("done", {"on": "2026-10-12"}), ("reopen", {"on": "2026-10-12"}),
                       ("skip", {"on": "2026-10-19"}), ("reschedule", {"day": "2026-10-13", "time": "08:00"}),
                       ("edit", {"title": "Gym!", "note": "", "duration_min": "", "repeat": "weekly",
                                 "weekdays": ["1"], "until": ""})]:
        response = client.post(f"/ui/planner/plans/{pid}/{path}", data=data)
        assert response.headers.get("HX-Trigger") == "planner-changed", (path, response.text)
    plan = client.get("/planner/plans").json()[0]
    assert (plan["title"], plan["day"], plan["time"]) == ("Gym!", "2026-10-13", "08:00:00")
    assert client.delete(f"/ui/planner/plans/{pid}").headers["HX-Trigger"] == "planner-changed"
    assert client.get("/planner/plans").json() == []


def test_refused_actions_give_an_error_toast(client):
    add(client, "Tandarts vr 14u")
    pid = plan_id(client, "Tandarts")
    response = client.post(f"/ui/planner/plans/{pid}/done", data={"on": "2026-10-10"})  # valt niet op za
    assert "toast-error" in response.text
    same = client.post(f"/ui/planner/plans/{pid}/reschedule", data={"day": "2026-10-09", "time": "14:00"})
    assert "HX-Trigger" not in same.headers and "toast-error" not in same.text  # niets veranderd


def test_plan_it_from_someday_and_back(client):
    add(client, "Learn Korean")
    pid = plan_id(client, "Learn Korean")
    client.post(f"/ui/planner/plans/{pid}/reschedule", data={"day": "2026-10-10", "time": ""})
    assert "Learn Korean" in client.get("/ui/planner/week").text
    client.post(f"/ui/planner/plans/{pid}/reschedule", data={"day": "", "time": ""})
    assert "Learn Korean" in client.get("/ui/planner/someday").text


# ---- .ics, herinneringen en het startscherm ----

def test_ics_download(client):
    add(client, "Tandarts vr 14u")
    response = client.get(f"/ui/planner/{plan_id(client, 'Tandarts')}.ics")
    assert response.headers["content-type"].startswith("text/calendar")
    assert 'attachment; filename="Tandarts.ics"' == response.headers["content-disposition"]
    assert "DTSTART:20261009T140000" in response.text and "TRIGGER:-P1D" in response.text
    assert client.get("/ui/planner/bestaat-niet.ics").status_code == 404


def test_due_returns_reminders_of_the_last_minute(client, monkeypatch):
    add(client, "Tandarts vr 14u")
    monkeypatch.setattr(schedule, "local_now", lambda: dt.datetime(2026, 10, 8, 14, 0, 30))
    [reminder] = client.get("/planner/due").json()
    assert reminder["text"] == "Tomorrow: Tandarts at 14:00" and reminder["key"].endswith("/2026-10-09/1")
    monkeypatch.setattr(schedule, "local_now", lambda: dt.datetime(2026, 10, 8, 14, 5))
    assert client.get("/planner/due").json() == []
    assert len(client.get("/planner/due", params={"minutes": 10}).json()) == 1


def test_reminder_settings_in_settings(client):
    assert 'hx-get="/ui/planner/reminders"' in client.get("/ui/settings").text
    panel = client.get("/ui/planner/reminders").text
    assert "First reminder" in panel and "Second reminder" in panel and 'value="09:00"' in panel
    response = client.post("/ui/planner/reminders", data={"first_enabled": "on", "first_minutes": "60",
                                                           "second_minutes": "180", "all_day_time": "08:00"})
    assert response.headers["HX-Trigger"] == "planner-changed"
    panel = client.get("/ui/planner/reminders").text
    assert 'value="60" selected' in panel and 'value="08:00"' in panel
    again = client.post("/ui/planner/reminders", data={"first_enabled": "on", "first_minutes": "60",
                                                        "second_minutes": "180", "all_day_time": "08:00"})
    assert "HX-Trigger" not in again.headers  # dezelfde instelling: geen event


def test_home_card_shows_today_with_at_most_three_plans(client):
    for text in ["Standup vandaag 11:00", "Lunch vandaag 12:30", "Call vandaag 15:00", "Gym vandaag 18:00",
                 "Verjaardag Sam vandaag"]:
        add(client, text)
    home = client.get("/ui/home").text
    card = home.split('href="#planner"', 1)[1].split("</a>", 1)[0]
    assert "Next: Standup at 11:00" in card and "5" in card
    assert card.count('class="agenda-row"') <= 2  # met "Next" erbij maximaal drie plannen
    assert "planner-changed from:body" in home


def test_planner_is_in_the_navigation_and_settings(client):
    index = client.get("/").text
    assert 'href="#planner"' in index and 'id="plan-dialog"' in index
    assert "planner:" in client.get("/static/trackly.js").text
    assert "Planner" in client.get("/ui/settings").text


def test_due_since_the_previous_check_never_reaches_before_it(client, monkeypatch):
    add(client, "Tandarts vr 14u")
    monkeypatch.setattr(schedule, "local_now", lambda: dt.datetime(2026, 10, 8, 14, 1))
    assert len(client.get("/planner/due", params={"since": "2026-10-08T13:59:30"}).json()) == 1
    assert client.get("/planner/due", params={"since": "2026-10-08T14:00:00"}).json() == []  # viel er net vóór


def test_done_someday_plans_are_listed_and_can_be_reopened(client):
    add(client, "Learn Korean")
    pid = plan_id(client, "Learn Korean")
    assert 'class="someday-done"' not in client.get("/ui/planner/someday").text  # nog niets af
    client.post(f"/ui/planner/plans/{pid}/done", data={"on": "2026-10-08"})
    page = client.get("/ui/planner/someday").text
    done = page.split('class="someday-done"', 1)[1]
    assert "Learn Korean" in done and "done Thu 8 Oct" in done and "Not done" in done
    assert f'hx-post="/ui/planner/plans/{pid}/reopen"' in done and '"on": "2026-10-08"' in done
    assert "Nothing left for someday" in page.split('class="someday-done"', 1)[0]
    response = client.post(f"/ui/planner/plans/{pid}/reopen", data={"on": "2026-10-08"})
    assert response.headers["HX-Trigger"] == "planner-changed"
    assert 'class="someday-done"' not in client.get("/ui/planner/someday").text
