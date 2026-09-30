import pytest

from app.eventstore import EventStore
from app.reading.aggregate import DomainError, ReadingSeries
from app.reading.commands import ChangeStatus, LogProgress, ReadingCommandHandler, StartSeries
from app.reading.events import Kind, ProgressLogged, SeriesStarted, Status, StatusChanged
from app.reading.projections import LibraryProjection


def make_handler():
    store = EventStore(":memory:", [SeriesStarted, ProgressLogged, StatusChanged])
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