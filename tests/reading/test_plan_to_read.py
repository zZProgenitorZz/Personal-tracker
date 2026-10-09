"""Plan to Read: series die je nog wilt beginnen. Een extra status, geen nieuw event."""
import pytest
from fastapi.testclient import TestClient

from app.eventstore import EventStore
from app.main import create_app
from app.reading.aggregate import ReadingSeries
from app.reading.commands import ChangeStatus, LogProgress, ReadingCommandHandler, StartSeries
from app.reading.events import (
    GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged,
)
from app.reading.projections import LibraryProjection, ReadingActivityProjection

EVENT_TYPES = [SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved, GenresChanged]
STARTED = SeriesStarted("1", "Omniscient Reader", Kind.NOVEL, "x", 0)
PLANNED = StatusChanged("1", Status.READING, Status.PLAN_TO_READ)


# ---- Aggregate ----

def test_old_events_stay_readable():
    # Given een serie uit de tijd vóór Plan to Read, when we hem opbouwen, then niets verandert.
    series = ReadingSeries([STARTED, StatusChanged("1", Status.READING, Status.ON_HOLD)])
    assert series.status is Status.ON_HOLD


def test_add_as_plan_to_read_uses_the_existing_events():
    # Given niets, when ik toevoeg als Plan to Read, then SeriesStarted + StatusChanged(READING → PLAN_TO_READ).
    series = ReadingSeries([])
    events = series.start("1", "Omniscient Reader", Kind.NOVEL, "x", 0, status=Status.PLAN_TO_READ)
    assert [type(e) for e in events] == [SeriesStarted, StatusChanged]
    assert (events[1].from_status, events[1].to_status) == (Status.READING, Status.PLAN_TO_READ)
    assert series.status is Status.PLAN_TO_READ


def test_logging_progress_on_plan_to_read_starts_reading():
    # Given een serie op Plan to Read, when ik voortgang log, then staat hij automatisch op Reading.
    series = ReadingSeries([STARTED, PLANNED])
    events = series.log_progress(3)
    assert [type(e) for e in events] == [StatusChanged, ProgressLogged]
    assert (events[0].from_status, events[0].to_status) == (Status.PLAN_TO_READ, Status.READING)
    assert series.status is Status.READING


@pytest.mark.parametrize("other", [s for s in Status if s is not Status.PLAN_TO_READ])
def test_plan_to_read_changes_to_and_from_every_status(other):
    # Van Plan to Read naar elke andere status...
    there = ReadingSeries([STARTED, PLANNED]).change_status(other)
    assert there[0].to_status is other
    # ...en andersom.
    start = [STARTED] if other is Status.READING else [STARTED, StatusChanged("1", Status.READING, other)]
    back = ReadingSeries(start).change_status(Status.PLAN_TO_READ)
    assert (back[0].from_status, back[0].to_status) == (other, Status.PLAN_TO_READ)


# ---- Projecties ----

def make_app():
    store = EventStore(":memory:", EVENT_TYPES)
    library, activity = LibraryProjection(), ReadingActivityProjection()
    store.subscribe(library.apply)
    store.subscribe(activity.apply)
    return ReadingCommandHandler(store, library), library, activity


def test_plan_to_read_is_not_currently_reading():
    handler, library, activity = make_app()
    handler.handle(StartSeries("Solo Leveling", Kind.MANHWA, "asura", 1))
    handler.handle(StartSeries("Omniscient Reader", Kind.NOVEL, "x", 50, status=Status.PLAN_TO_READ))
    assert [e.title for e in library.currently_reading()] == ["Solo Leveling"]
    assert [e.title for e in library.by_status(Status.PLAN_TO_READ)] == ["Omniscient Reader"]
    assert activity.total_chapters() == 0  # het beginhoofdstuk telt niet als gelezen


def test_starting_from_the_backlog_counts_only_what_you_read():
    handler, library, activity = make_app()
    [started, _] = handler.handle(StartSeries("Omniscient Reader", Kind.NOVEL, "x", 0, status=Status.PLAN_TO_READ))
    handler.handle(LogProgress(started.series_id, 4))
    assert [e.title for e in library.currently_reading()] == ["Omniscient Reader"]
    assert activity.total_chapters() == 4


# ---- Webpagina ----

@pytest.fixture
def client():
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def add(client, title, status="reading", genres=()):
    client.post("/ui/reading/series", data={"title": title, "kind": "manhwa", "source": "x", "start_chapter": "1",
                                            "status": status, "genres": list(genres)})
    return next(e["series_id"] for e in client.get("/reading/library").json() if e["title"] == title)


def test_plan_to_read_is_everywhere_a_status_can_be_chosen(client):
    add(client, "Solo Leveling")
    assert 'value="plan_to_read"' in client.get("/ui/reading/add-form").text
    assert 'value="plan_to_read"' in client.get("/ui/reading/library/grid").text  # statusmenu op de kaart
    assert 'value="plan_to_read"' in client.get("/ui/reading/library").text      # filter
    form = client.get("/ui/reading/add-form").text
    assert form.index('value="reading" checked') < form.index('value="plan_to_read"')  # standaard blijft Reading


def test_plan_to_read_does_not_count_as_reading(client):
    add(client, "Backlog", "plan_to_read")
    assert "Plan to read" in client.get("/ui/reading/library/grid?status=plan_to_read").text
    dashboard = client.get("/ui/reading/dashboard").text
    assert '<div class="stat-value">0' in dashboard  # Currently reading
    assert "Backlog" not in dashboard.split("Currently reading", 2)[2].split("</section>", 1)[0]
    home = client.get("/ui/home").text
    assert "<b>0</b><span>reading now" in home and "Backlog" not in home


# ---- "Pick something" ----

def test_pick_button_only_with_a_backlog(client):
    add(client, "Solo Leveling")
    assert "Pick something" not in client.get("/ui/reading/library").text
    add(client, "Backlog", "plan_to_read")
    assert "Pick something" in client.get("/ui/reading/library").text


def test_pick_shows_a_title_from_the_backlog(client):
    add(client, "Solo Leveling")
    sid = add(client, "Omniscient Reader", "plan_to_read", ["Fantasy"])
    page = client.get("/ui/reading/pick").text
    assert "Omniscient Reader" in page and "Fantasy" in page and "Solo Leveling" not in page
    assert f'hx-post="/ui/reading/series/{sid}/status"' in page and '"status": "reading"' in page


def test_another_one_is_never_the_same(client):
    a = add(client, "A", "plan_to_read")
    add(client, "B", "plan_to_read")
    for _ in range(20):
        assert ">B<" in client.get("/ui/reading/pick", params={"exclude": a}).text


def test_only_one_in_the_backlog_has_no_another_one(client):
    a = add(client, "A", "plan_to_read")
    page = client.get("/ui/reading/pick", params={"exclude": a}).text
    assert ">A<" in page and "Another one" not in page


def test_pick_with_a_genre(client):
    add(client, "A", "plan_to_read", ["Romance"])
    add(client, "B", "plan_to_read", ["Action"])
    for _ in range(10):
        assert ">B<" in client.get("/ui/reading/pick", params={"genre": "Action"}).text
    assert "Nothing in your backlog" in client.get("/ui/reading/pick", params={"genre": "Horror"}).text


def test_pick_with_an_empty_backlog(client):
    assert "Nothing in your backlog" in client.get("/ui/reading/pick").text


def test_start_from_the_pick_uses_change_status(client):
    sid = add(client, "A", "plan_to_read")
    response = client.post(f"/ui/reading/series/{sid}/status", data={"status": "reading"})
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert client.get("/reading/library").json()[0]["status"] == "reading"
