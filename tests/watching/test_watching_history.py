"""Watching history: what was watched and when, removed shows included. Built from existing events only."""
from datetime import date, datetime

from app.watching.events import ShowAdded, ShowRemoved, ShowStatusChanged, WatchKind, WatchStatus
from app.watching.projections import WatchHistoryProjection

W, C, R = WatchStatus.WATCHING, WatchStatus.COMPLETED, WatchStatus.REWATCHING


def at(y, m, d, hour=12) -> datetime:
    """A moment in the local time zone, the way the projections count days."""
    return datetime(y, m, d, hour).astimezone()


def added(sid, title, when, status=W, kind=WatchKind.SERIES):
    return ShowAdded(sid, title, kind, status, at=when)


def change(sid, frm, to, when):
    return ShowStatusChanged(sid, frm, to, at=when)


def given(*events) -> WatchHistoryProjection:
    projection = WatchHistoryProjection()
    for event in events:
        projection.apply(event)
    return projection


def test_completed_rewatched_and_completed_again():
    h = given(added("1", "Frieren", at(2026, 1, 5), kind=WatchKind.ANIME),
              change("1", W, C, at(2026, 3, 1)),
              change("1", C, R, at(2026, 8, 1)),
              change("1", R, C, at(2026, 9, 20)))
    timeline = h.timeline(h.key_of("1"))
    assert (timeline.added, timeline.start_status) == (date(2026, 1, 5), W)
    assert (timeline.times_completed, timeline.times_rewatched) == (2, 1)
    assert timeline.completed == [date(2026, 3, 1), date(2026, 9, 20)]
    assert len(timeline.changes) == 3


def test_added_as_completed_counts_once_with_an_unknown_date():
    h = given(added("1", "Dune", at(2026, 2, 1), status=C, kind=WatchKind.MOVIE))
    timeline = h.timeline(h.key_of("1"))
    assert (timeline.times_completed, timeline.completed, timeline.changes) == (1, [None], [])


def test_a_show_on_watching_from_july_to_september_was_watched_in_august():
    h = given(added("1", "Severance", at(2026, 7, 10)),
              change("1", W, C, at(2026, 9, 5)),
              added("2", "Later", at(2026, 9, 1)),
              added("3", "Done before", at(2026, 6, 1)), change("3", W, C, at(2026, 6, 30)))
    rows = h.between(date(2026, 8, 1), date(2026, 8, 31))
    assert [(r.title, r.finished_on, r.rewatch) for r in rows] == [("Severance", None, False)]
    september = h.between(date(2026, 9, 1), date(2026, 9, 30))
    assert [(r.title, r.finished_on) for r in september] == [("Severance", date(2026, 9, 5)), ("Later", None)]


def test_a_finished_rewatch_is_marked_as_rewatch():
    h = given(added("1", "Frieren", at(2026, 1, 5), status=C), change("1", C, R, at(2026, 8, 1)),
              change("1", R, C, at(2026, 8, 20)))
    assert [(r.finished_on, r.rewatch) for r in h.between(date(2026, 8, 1), date(2026, 8, 31))] == [
        (date(2026, 8, 20), True)]


def test_removed_shows_stay_and_a_film_and_a_series_with_one_title_are_apart():
    h = given(added("film", "Dune", at(2026, 1, 1), kind=WatchKind.MOVIE),
              added("series", "Dune", at(2026, 1, 2)),
              ShowRemoved("film", "Dune", at=at(2026, 2, 1)),
              added("film2", "dune", at(2026, 3, 1), kind=WatchKind.MOVIE))  # re-added
    assert h.key_of("film") == h.key_of("film2") != h.key_of("series")
    assert h.timeline(h.key_of("film2")).added == date(2026, 1, 1)
    # After removing, a show isn't watched anymore until it is added again.
    assert [r.kind for r in h.between(date(2026, 2, 10), date(2026, 2, 20))] == [WatchKind.SERIES]
