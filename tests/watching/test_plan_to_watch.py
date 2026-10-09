"""Plan to Watch: titels die je nog wilt kijken. Een extra status, geen nieuw event."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.eventstore import EventStore
from app.main import create_app
from app.watching.aggregate import WatchItem
from app.watching.commands import AddShow, ChangeShowStatus, WatchingCommandHandler
from app.watching.events import ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged, WatchKind, WatchStatus
from app.watching.projections import WatchActivityProjection, WatchlistProjection
from app.watching.web import watching_summary

EVENT_TYPES = [ShowAdded, ShowStatusChanged, ShowGenresChanged, ShowRemoved]
PLANNED = ShowAdded("1", "Dune", WatchKind.MOVIE, WatchStatus.PLAN_TO_WATCH)


# ---- Aggregate ----

def test_add_as_plan_to_watch_is_just_show_added():
    item = WatchItem([])
    events = item.add("1", "Dune", WatchKind.MOVIE, WatchStatus.PLAN_TO_WATCH)
    assert [type(e) for e in events] == [ShowAdded]
    assert events[0].status is WatchStatus.PLAN_TO_WATCH


@pytest.mark.parametrize("other", [s for s in WatchStatus if s is not WatchStatus.PLAN_TO_WATCH])
def test_plan_to_watch_changes_to_and_from_every_status(other):
    assert WatchItem([PLANNED]).change_status(other)[0].to_status is other
    back = WatchItem([ShowAdded("1", "Dune", WatchKind.MOVIE, other)]).change_status(WatchStatus.PLAN_TO_WATCH)
    assert (back[0].from_status, back[0].to_status) == (other, WatchStatus.PLAN_TO_WATCH)


# ---- Projecties ----

def make_app():
    store = EventStore(":memory:", EVENT_TYPES)
    watchlist, activity = WatchlistProjection(), WatchActivityProjection()
    store.subscribe(watchlist.apply)
    store.subscribe(activity.apply)
    return WatchingCommandHandler(store, watchlist), watchlist, activity


def test_plan_to_watch_is_not_watching():
    handler, watchlist, activity = make_app()
    handler.handle(AddShow("Frieren", WatchKind.ANIME))
    handler.handle(AddShow("Dune", WatchKind.MOVIE, WatchStatus.PLAN_TO_WATCH))
    assert [e.title for e in watchlist.currently_watching()] == ["Frieren"]
    assert watching_summary(watchlist, activity)["stats"][0] == (1, "watching now")
    assert sum(activity.per_day().values()) == 0  # toevoegen is nog geen afronding


def test_plan_to_watch_to_completed_counts_as_finished():
    handler, watchlist, activity = make_app()
    [added] = handler.handle(AddShow("Dune", WatchKind.MOVIE, WatchStatus.PLAN_TO_WATCH))
    handler.handle(ChangeShowStatus(added.show_id, WatchStatus.COMPLETED))
    assert activity.per_day() == {date.today(): 1}
    assert watching_summary(watchlist, activity)["stats"][1] == (1, "finished this month")


# ---- Webpagina ----

@pytest.fixture
def client():
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def add(client, title, status="watching", genres=()):
    client.post("/ui/watching/shows", data={"title": title, "kind": "movie", "status": status, "genres": list(genres)})
    return next(e["show_id"] for e in client.get("/watching/list").json() if e["title"] == title)


def test_plan_to_watch_is_everywhere_a_status_can_be_chosen(client):
    add(client, "Frieren")
    form = client.get("/ui/watching/add-form").text
    assert 'value="plan_to_watch"' in form
    assert form.index('value="watching" checked') < form.index('value="plan_to_watch"')  # standaard blijft Watching
    assert 'value="plan_to_watch"' in client.get("/ui/watching/library/grid").text
    assert 'value="plan_to_watch"' in client.get("/ui/watching/library").text


def test_plan_to_watch_not_on_home_as_watching(client):
    add(client, "Dune", "plan_to_watch")
    home = client.get("/ui/home").text
    assert "<b>0</b><span>watching now" in home and "Dune" not in home
    assert "Plan to watch" in client.get("/ui/watching/library/grid?status=plan_to_watch").text


def test_pick_button_only_with_a_backlog(client):
    add(client, "Frieren")
    assert "Pick something" not in client.get("/ui/watching/library").text
    add(client, "Dune", "plan_to_watch")
    assert "Pick something" in client.get("/ui/watching/library").text


def test_pick_and_another_one(client):
    a = add(client, "A", "plan_to_watch", ["Drama"])
    add(client, "B", "plan_to_watch")
    add(client, "C")
    page = client.get("/ui/watching/pick", params={"exclude": a}).text
    assert ">B<" in page and "Another one" in page
    page = client.get("/ui/watching/pick", params={"genre": "Drama"}).text
    assert ">A<" in page and f'hx-post="/ui/watching/shows/{a}/status"' in page and '"status": "watching"' in page
    assert "Nothing in your backlog" in client.get("/ui/watching/pick", params={"genre": "Horror"}).text
