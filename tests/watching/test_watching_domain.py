"""Watching: films, series en anime. Alleen status en genres, geen afleveringen."""
from datetime import date, datetime, time, timedelta

import pytest

from app.domain import DomainError
from app.eventstore import EventStore
from app.watching.aggregate import WatchItem
from app.watching.commands import AddShow, ChangeShowStatus, RemoveShow, SetShowGenres, WatchingCommandHandler
from app.watching.events import (
    ShowAdded, ShowGenresChanged, ShowRemoved, ShowStatusChanged, WatchKind, WatchStatus,
)
from app.watching.genres import WATCH_GENRES
from app.watching.projections import WatchActivityProjection, WatchlistProjection

EVENT_TYPES = [ShowAdded, ShowStatusChanged, ShowGenresChanged, ShowRemoved]
ADDED = ShowAdded("1", "Frieren", WatchKind.ANIME, WatchStatus.WATCHING)


def make_app():
    store = EventStore(":memory:", EVENT_TYPES)
    watchlist, activity = WatchlistProjection(), WatchActivityProjection()
    store.subscribe(watchlist.apply)
    store.subscribe(activity.apply)
    return WatchingCommandHandler(store, watchlist), watchlist, activity, store


# ---- Aggregate ----

def test_add_with_status_and_genres():
    events = WatchItem([]).add("1", "Dune", WatchKind.MOVIE, WatchStatus.COMPLETED, genres=["Sci-Fi", "Adventure"])
    assert [type(e) for e in events] == [ShowAdded, ShowGenresChanged]
    assert events[0].status is WatchStatus.COMPLETED
    assert events[1].genres == ["Adventure", "Sci-Fi"]


@pytest.mark.parametrize("start, target", [
    (WatchStatus.WATCHING, WatchStatus.COMPLETED), (WatchStatus.COMPLETED, WatchStatus.WATCHING),
    (WatchStatus.ON_HOLD, WatchStatus.DROPPED), (WatchStatus.DROPPED, WatchStatus.ON_HOLD),
])
def test_every_status_can_change(start, target):
    item = WatchItem([ShowAdded("1", "X", WatchKind.SERIES, start)])
    assert item.change_status(target)[0].to_status is target


def test_same_status_is_refused():
    with pytest.raises(DomainError):
        WatchItem([ADDED]).change_status(WatchStatus.WATCHING)


def test_unknown_genre_is_refused():
    with pytest.raises(DomainError, match="Ninja"):
        WatchItem([ADDED]).set_genres(["Ninja"])


def test_removed_show_cannot_change():
    item = WatchItem([ADDED, ShowRemoved("1", "Frieren")])
    for action in (lambda: item.change_status(WatchStatus.COMPLETED), lambda: item.set_genres([]), item.remove):
        with pytest.raises(DomainError):
            action()


# ---- Commands en projecties ----

def test_same_title_and_kind_cannot_be_added_twice():
    handler, *_ = make_app()
    handler.handle(AddShow("Dune", WatchKind.MOVIE))
    with pytest.raises(DomainError):
        handler.handle(AddShow(" dune ", WatchKind.MOVIE))


def test_same_title_as_another_kind_is_fine():
    handler, watchlist, *_ = make_app()
    handler.handle(AddShow("Dune", WatchKind.MOVIE))
    handler.handle(AddShow("Dune", WatchKind.SERIES))
    assert len(watchlist.all()) == 2


def test_removed_title_can_be_added_again():
    handler, watchlist, *_ = make_app()
    [added] = handler.handle(AddShow("Dune", WatchKind.MOVIE))
    handler.handle(RemoveShow(added.show_id))
    handler.handle(AddShow("Dune", WatchKind.MOVIE))
    assert len(watchlist.all()) == 1


def test_watchlist_follows_the_events_and_can_be_rebuilt():
    handler, watchlist, _, store = make_app()
    [added, _] = handler.handle(AddShow("Frieren", WatchKind.ANIME, genres=("Fantasy",)))
    handler.handle(ChangeShowStatus(added.show_id, WatchStatus.COMPLETED))
    handler.handle(SetShowGenres(added.show_id, ("Adventure", "Fantasy")))

    [entry] = watchlist.all()
    assert (entry.status, entry.genres) == (WatchStatus.COMPLETED, ["Adventure", "Fantasy"])
    assert watchlist.currently_watching() == []
    rebuilt = WatchlistProjection()
    for event in store.load_all():
        rebuilt.apply(event)
    assert rebuilt.all() == watchlist.all()


def test_only_marking_completed_counts_as_finished():
    handler, _, activity, _ = make_app()
    handler.handle(AddShow("Already seen", WatchKind.MOVIE, WatchStatus.COMPLETED))  # geschiedenis
    [added] = handler.handle(AddShow("Frieren", WatchKind.ANIME))
    handler.handle(ChangeShowStatus(added.show_id, WatchStatus.COMPLETED))
    assert sum(activity.per_day().values()) == 1


def test_finished_per_day_uses_local_date():
    activity = WatchActivityProjection()
    day = date(2026, 10, 1)
    activity.apply(ShowStatusChanged("1", WatchStatus.WATCHING, WatchStatus.COMPLETED,
                                     at=datetime.combine(day, time(12)).astimezone()))
    assert activity.per_day() == {day: 1}


def test_genre_list_is_sorted_and_maps_source_names():
    assert WATCH_GENRES.names == sorted(set(WATCH_GENRES.names))
    assert WATCH_GENRES.from_source(["Action & Adventure", "Sci-Fi & Fantasy", "War & Politics", "Kids"]) == [
        "Action", "Adventure", "Family", "Fantasy", "Sci-Fi", "War"]
