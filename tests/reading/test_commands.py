import pytest

from app.eventstore import EventStore
from app.reading.aggregate import DomainError, ReadingSeries
from app.reading.commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, StartSeries
from app.reading.events import Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged
from app.reading.projections import LibraryProjection


def make_handler():
    store = EventStore(":memory:", [SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved])
    library = LibraryProjection()
    store.subscribe(library.apply)
    return ReadingCommandHandler(store, library), store


def start_solo_leveling(handler) -> str:
    events = handler.handle(StartSeries("Solo Leveling", Kind.MANHWA, "asura", 1))
    return events[0].series_id


def test_started_series_is_stored():
    handler, store = make_handler()
    series_id = start_solo_leveling(handler)
    assert [type(e) for e in store.load_stream(series_id)] == [SeriesStarted]


def test_full_flow_rebuilds_correct_state():
    handler, store = make_handler()
    series_id = start_solo_leveling(handler)

    handler.handle(LogProgress(series_id, 57))
    handler.handle(ChangeStatus(series_id, Status.ON_HOLD))
    handler.handle(LogProgress(series_id, 60))

    series = ReadingSeries(store.load_stream(series_id))
    assert series.current_chapter == 60
    assert series.status is Status.READING


def test_rejected_command_stores_nothing():
    handler, store = make_handler()
    series_id = start_solo_leveling(handler)

    with pytest.raises(DomainError):
        handler.handle(ChangeStatus(series_id, Status.READING))

    assert len(store.load_all()) == 1


def test_unknown_series_is_rejected():
    handler, store = make_handler()
    with pytest.raises(DomainError):
        handler.handle(LogProgress("bestaat-niet", 5))
    assert store.load_all() == []

def test_removed_title_can_be_added_again_as_new_series():
    handler, store = make_handler()
    old_id = start_solo_leveling(handler)
    handler.handle(RemoveSeries(old_id))

    [started] = handler.handle(StartSeries(" solo leveling", Kind.MANHWA, "asura", 1))

    assert started.series_id != old_id
    assert [type(e) for e in store.load_all()] == [SeriesStarted, SeriesRemoved, SeriesStarted]
    assert ReadingSeries(store.load_stream(old_id)).removed  # de oude blijft verwijderd


def test_title_in_library_still_cannot_be_added_twice():
    handler, _ = make_handler()
    old_id = start_solo_leveling(handler)
    handler.handle(RemoveSeries(old_id))
    start_solo_leveling(handler)

    with pytest.raises(DomainError):
        start_solo_leveling(handler)
