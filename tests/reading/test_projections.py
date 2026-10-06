from datetime import date, datetime, time, timedelta

import pytest

from app.eventstore import EventStore
from app.reading.aggregate import DomainError
from app.reading.commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, StartSeries
from app.reading.events import Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged
from app.reading.projections import LibraryProjection, ReadingActivityProjection

EVENT_TYPES = [SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved]


def make_app():
    store = EventStore(":memory:", EVENT_TYPES)
    library = LibraryProjection()
    store.subscribe(library.apply)
    return ReadingCommandHandler(store, library), library, store


def start(handler, title: str) -> str:
    return handler.handle(StartSeries(title, Kind.MANHWA, "asura", 1))[0].series_id


def test_currently_reading_shows_progress():
    handler, library, _ = make_app()
    series_id = start(handler, "Solo Leveling")
    handler.handle(LogProgress(series_id, 57))

    [entry] = library.currently_reading()
    assert entry.title == "Solo Leveling"
    assert entry.current_chapter == 57


def test_on_hold_leaves_currently_reading():
    handler, library, _ = make_app()
    series_id = start(handler, "Solo Leveling")
    handler.handle(ChangeStatus(series_id, Status.ON_HOLD))

    assert library.currently_reading() == []
    assert len(library.by_status(Status.ON_HOLD)) == 1


def test_cannot_start_same_title_twice():
    handler, _, _ = make_app()
    start(handler, "Solo Leveling")
    with pytest.raises(DomainError):
        start(handler, "  solo leveling ")


def test_activity_counts_chapters_read():
    activity = ReadingActivityProjection()
    activity.apply(ProgressLogged("1", 57, 1))
    activity.apply(ProgressLogged("1", 60, 57))
    assert sum(activity.per_day().values()) == 59


def test_going_back_does_not_count_as_reading():
    activity = ReadingActivityProjection()
    activity.apply(ProgressLogged("1", 50, 57))
    assert activity.per_day() == {}


def test_library_can_be_rebuilt_from_event_store():
    handler, live, store = make_app()
    series_id = start(handler, "Solo Leveling")
    handler.handle(LogProgress(series_id, 57))

    rebuilt = LibraryProjection()
    for event in store.load_all():
        rebuilt.apply(event)

    assert rebuilt.all() == live.all()

def logged_on(day: date, chapter: float, previous: float) -> ProgressLogged:
    return ProgressLogged("1", chapter, previous, at=datetime.combine(day, time(12)).astimezone())


def test_total_chapters_sums_all_days():
    activity = ReadingActivityProjection()
    activity.apply(logged_on(date(2026, 9, 1), 10, 1))
    activity.apply(logged_on(date(2026, 9, 3), 15, 10))
    assert activity.total_chapters() == 14


def test_streak_counts_consecutive_days_up_to_today():
    today = date(2026, 9, 30)
    activity = ReadingActivityProjection()
    for offset in range(3):
        activity.apply(logged_on(today - timedelta(days=offset), 10 + offset, 9 + offset))
    activity.apply(logged_on(today - timedelta(days=5), 2, 1))
    assert activity.streak(today) == 3


def test_streak_survives_until_end_of_today():
    today = date(2026, 9, 30)
    activity = ReadingActivityProjection()
    activity.apply(logged_on(today - timedelta(days=1), 2, 1))
    assert activity.streak(today) == 1


def test_streak_breaks_after_a_missed_day():
    today = date(2026, 9, 30)
    activity = ReadingActivityProjection()
    activity.apply(logged_on(today - timedelta(days=2), 2, 1))
    assert activity.streak(today) == 0


def test_removed_series_leaves_library_but_keeps_activity():
    handler, library, store = make_app()
    activity = ReadingActivityProjection()
    store.subscribe(activity.apply)
    series_id = start(handler, "Solo Leveling")
    handler.handle(LogProgress(series_id, 11))
    handler.handle(RemoveSeries(series_id))

    assert library.all() == []
    assert library.get(series_id) is None
    assert library.find_by_title("solo leveling") is not None
    assert activity.total_chapters() == 10
