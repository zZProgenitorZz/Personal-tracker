"""Planner · Upcoming: al je komende plannen in één lijst. Alleen lezen uit AgendaProjection."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import app.planner.schedule as schedule
from app.main import create_app
from app.planner.events import Frequency, PlanAdded, PlanDone, PlanRemoved, PlanSkipped, Repeat
from app.planner.parse import describe_repeat
from app.planner.projections import AgendaProjection
from app.planner.schedule import next_occurrence

TODAY = dt.date(2026, 10, 8)  # donderdag
D = lambda m, d, y=2026: dt.date(y, m, d)  # noqa: E731


def given(*events) -> AgendaProjection:
    agenda = AgendaProjection()
    for event in events:
        agenda.apply(event)
    return agenda


def plan(pid, title, day=TODAY, time=None, repeat=None, note=""):
    return PlanAdded(pid, title, day, time, None, note, repeat)


# ---- De eerstvolgende keer ----

def test_next_occurrence_of_a_single_plan():
    agenda = given(plan("1", "Dentist", D(10, 9)), plan("2", "Old", D(10, 1)), plan("3", "Done", D(10, 9)),
                   PlanDone("3", D(10, 9)))
    assert next_occurrence(agenda.get("1"), TODAY) == D(10, 9)
    assert next_occurrence(agenda.get("2"), TODAY) is None   # voorbij
    assert next_occurrence(agenda.get("3"), TODAY) is None   # af


def test_next_occurrence_skips_skipped_and_done_times():
    gym = Repeat(Frequency.WEEKLY, (0, 3))  # ma en do
    agenda = given(plan("1", "Gym", D(10, 1), repeat=gym), PlanDone("1", TODAY), PlanSkipped("1", D(10, 12)))
    assert next_occurrence(agenda.get("1"), TODAY) == D(10, 15)


def test_next_occurrence_of_a_repeat_that_has_not_started_or_has_ended():
    agenda = given(plan("1", "Course", D(11, 2), repeat=Repeat(Frequency.WEEKLY, (0,))),
                   plan("2", "Over", D(9, 1), repeat=Repeat(Frequency.DAILY, until=D(10, 7))))
    assert next_occurrence(agenda.get("1"), TODAY) == D(11, 2)
    assert next_occurrence(agenda.get("2"), TODAY) is None


def test_someday_has_no_next_occurrence():
    assert next_occurrence(given(plan("1", "Learn Korean", None)).get("1"), TODAY) is None


# ---- upcoming(today) ----

def test_upcoming_one_offs_are_sorted_and_past_done_and_someday_left_out():
    agenda = given(plan("1", "Dentist", D(10, 9), dt.time(14)), plan("2", "Birthday", D(10, 9)),
                   plan("3", "Standup", TODAY, dt.time(9)), plan("4", "Past", D(10, 1)),
                   plan("5", "Done", D(10, 20)), PlanDone("5", D(10, 20)),
                   plan("6", "Someday", None), plan("7", "Gone", D(10, 10)), PlanRemoved("7", "Gone"))
    one_offs, repeating = agenda.upcoming(TODAY)
    assert [o.title for o in one_offs] == ["Standup", "Birthday", "Dentist"]  # datum, dan hele dag eerst
    assert repeating == []


def test_upcoming_repeats_appear_once_in_order_of_their_next_time():
    agenda = given(plan("1", "Gym", D(9, 1), dt.time(18), Repeat(Frequency.WEEKLY, (0, 3))),
                   plan("2", "Rent", D(9, 1), repeat=Repeat(Frequency.MONTHLY)),
                   plan("3", "Course", D(11, 2), dt.time(19), Repeat(Frequency.WEEKLY, (0,))),
                   plan("4", "Over", D(9, 1), repeat=Repeat(Frequency.DAILY, until=D(10, 7))),
                   plan("5", "Vitamins", D(9, 1), dt.time(8), Repeat(Frequency.DAILY)))
    one_offs, repeating = agenda.upcoming(TODAY)
    assert one_offs == []
    assert [(p.title, next_day) for p, next_day in repeating] == [
        ("Vitamins", TODAY), ("Gym", TODAY), ("Rent", D(11, 1)), ("Course", D(11, 2))]


# ---- Hoe een herhaling heet ----

def test_describe_repeat_with_the_day_of_the_month():
    assert describe_repeat(Repeat(Frequency.MONTHLY), D(9, 12)) == "every month on the 12th"
    assert describe_repeat(Repeat(Frequency.MONTHLY), D(9, 1)) == "every month on the 1st"
    assert describe_repeat(Repeat(Frequency.MONTHLY), D(9, 22)) == "every month on the 22nd"
    assert describe_repeat(Repeat(Frequency.MONTHLY), D(9, 13)) == "every month on the 13th"
    assert describe_repeat(Repeat(Frequency.WEEKLY, (0, 3), D(12, 31)), D(9, 1)) == "every Mon, Thu until 31 Dec 2026"
    assert describe_repeat(Repeat(Frequency.DAILY)) == "every day"


# ---- De pagina ----

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(schedule, "local_now", lambda: dt.datetime(2026, 10, 8, 10, 0))
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def add(client, text, **more):
    assert "toast-error" not in client.post("/ui/planner/plans", data={"text": text, **more}).text


def test_upcoming_is_a_tab_after_week(client):
    page = client.get("/ui/planner/upcoming").text
    tabs = page.split('class="tracker-tabs"', 1)[1].split("</nav>", 1)[0]
    assert tabs.index("Week") < tabs.index("Upcoming") < tabs.index("Day") < tabs.index("Someday")
    assert 'href="#planner/upcoming"' in tabs and "upcoming:" not in page
    assert '"upcoming"' in client.get("/static/trackly.js").text
    assert "planner-changed from:body" in page and 'id="plan-text"' in page  # ververst zich, en snel invoeren


def test_upcoming_groups_one_offs_by_when(client):
    for text in ["Standup vandaag 11:00", "Dentist morgen 14:00", "Haircut za", "Yoga next week tue 18:00",
                 "Party 20 nov", "Ski 5 jan", "Learn Korean"]:
        add(client, text)
    page = client.get("/ui/planner/upcoming").text
    heads = [h.split("<", 1)[0].strip() for h in page.split('class="wrapped-sub upcoming-head">')[1:]]
    assert heads == ["Today", "Tomorrow", "This week", "Next week", "November", "January 2027"]
    lijst = page.split('class="upcoming-tools"', 1)[1]  # na het invoerveld (dat noemt "Dentist" als voorbeeld)
    assert lijst.index("Standup") < lijst.index("Dentist") < lijst.index("Haircut") < lijst.index("Yoga") \
        < lijst.index("Party") < lijst.index("Ski")
    assert "Learn Korean" not in lijst
    totals = page.split('upcoming-totals">', 1)[1].split("</p>", 1)[0]
    assert "6 plans" in totals and "0 repeating" in totals and "1 someday" in totals and 'href="#planner/someday"' in totals


def test_upcoming_repeating_block(client):
    add(client, "Gym elke ma en do 18:00", note="Legs")
    add(client, "Huur elke maand 12 okt")
    page = client.get("/ui/planner/upcoming").text
    block = page.split(">Repeating<", 1)[1]
    assert "Every Mon, Thu · 18:00" in block and "Every month on the 12th" in block
    assert "Next: Mon 12 Oct" in block and "Next: Thu 8 Oct" in block and "Legs" in block and "data-repeat" in block
    assert block.index("Gym") < block.index("Huur")  # gym valt eerder (vandaag 18:00)


def test_a_click_opens_the_existing_dialog_with_the_next_time(client):
    add(client, "Gym elke ma 18:00")
    add(client, "Dentist morgen 14:00")
    plans = {p["title"]: p["plan_id"] for p in client.get("/planner/plans").json()}
    page = client.get("/ui/planner/upcoming").text
    assert f'hx-get="/ui/planner/plans/{plans["Gym"]}?on=2026-10-12"' in page
    assert f'hx-get="/ui/planner/plans/{plans["Dentist"]}?on=2026-10-09"' in page


def test_search_field_and_empty_state(client):
    page = client.get("/ui/planner/upcoming").text
    assert "Nothing coming up" in page
    add(client, "Dentist morgen 14:00", note="Bring card")
    page = client.get("/ui/planner/upcoming").text
    assert "data-upcoming-search" in page and 'data-search="dentist bring card"' in page
