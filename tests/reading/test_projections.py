import json
from datetime import date, datetime, time, timedelta

import pytest

from app.eventstore import EventStore
from app.reading.aggregate import DomainError
from app.reading.commands import ChangeStatus, LogProgress, ReadingCommandHandler, RemoveSeries, SetGenres, StartSeries
from app.reading.events import GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged
from app.reading.projections import LibraryProjection, ReadingActivityProjection

EVENT_TYPES = [SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved, GenresChanged]


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
    assert library.find_by_title("solo leveling") is None  # titel is weer vrij
    assert activity.total_chapters() == 10


def test_re_added_series_starts_fresh_but_history_still_counts():
    handler, library, store = make_app()
    activity = ReadingActivityProjection()
    store.subscribe(activity.apply)
    old_id = start(handler, "Solo Leveling")
    handler.handle(LogProgress(old_id, 11))
    handler.handle(RemoveSeries(old_id))

    new_id = start(handler, "Solo Leveling")

    [entry] = library.all()
    assert entry.series_id == new_id and entry.current_chapter == 1
    assert activity.total_chapters() == 10

    rebuilt = LibraryProjection()
    for event in store.load_all():
        rebuilt.apply(event)
    assert rebuilt.all() == library.all()


def test_old_series_started_without_cover_still_loads():
    # Zo staan events in de database van vóór de cover-functie: zonder "cover".
    store = EventStore(":memory:", EVENT_TYPES)
    old = {"series_id": "1", "title": "Solo Leveling", "kind": "manhwa", "source": "asura",
           "start_chapter": 1.0, "at": "2026-09-30T13:29:33+00:00"}
    store._conn.execute(
        "INSERT INTO events (stream_id, type, data, at) VALUES (?, ?, ?, ?)",
        ("1", "SeriesStarted", json.dumps(old), old["at"]),
    )

    [event] = store.load_all()
    assert event.cover is None

    library = LibraryProjection()
    library.apply(event)
    assert library.get("1").cover is None


def test_cover_survives_storage_and_reaches_library():
    handler, library, store = make_app()
    [started] = handler.handle(StartSeries("Solo Leveling", Kind.MANHWA, "asura", 1, cover="abc.webp"))

    assert store.load_all()[0].cover == "abc.webp"
    assert library.get(started.series_id).cover == "abc.webp"


def test_finished_series_added_as_completed_does_not_count_as_reading_today():
    handler, library, store = make_app()
    activity = ReadingActivityProjection()
    store.subscribe(activity.apply)
    handler.handle(StartSeries("Lord of the Mysteries", Kind.NOVEL, "x", 1432, status=Status.COMPLETED))

    [entry] = library.all()
    assert entry.status is Status.COMPLETED and entry.current_chapter == 1432
    assert library.currently_reading() == []
    assert activity.total_chapters() == 0


def test_genres_reach_the_library_and_survive_a_rebuild():
    handler, library, store = make_app()
    [started, _] = handler.handle(StartSeries("Solo Leveling", Kind.MANHWA, "asura", 1, genres=["Fantasy", "Action"]))
    handler.handle(SetGenres(started.series_id, ["Action", "Game"]))

    assert library.get(started.series_id).genres == ["Action", "Game"]
    rebuilt = LibraryProjection()
    for event in store.load_all():
        rebuilt.apply(event)
    assert rebuilt.all() == library.all()


def test_old_series_have_no_genres():
    handler, library, _ = make_app()
    series_id = start(handler, "Solo Leveling")
    assert library.get(series_id).genres == []
