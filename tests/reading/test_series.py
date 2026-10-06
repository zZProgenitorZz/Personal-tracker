import pytest

from app.reading.aggregate import DomainError, ReadingSeries
from app.reading.events import GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged

STARTED = SeriesStarted("1", "Solo Leveling", Kind.MANHWA, "asura", 1)


def test_start_new_series():
    series = ReadingSeries([])
    events = series.start("1", "Solo Leveling", Kind.MANHWA, "asura", 1)
    assert [type(e) for e in events] == [SeriesStarted]
    assert series.status is Status.READING


def test_cannot_start_same_series_twice():
    series = ReadingSeries([STARTED])
    with pytest.raises(DomainError):
        series.start("1", "Solo Leveling", Kind.MANHWA, "asura", 1)


def test_log_progress_while_reading():
    series = ReadingSeries([STARTED])
    events = series.log_progress(57)
    assert [type(e) for e in events] == [ProgressLogged]
    assert events[0].chapter == 57
    assert events[0].previous_chapter == 1


def test_cannot_log_progress_on_unknown_series():
    series = ReadingSeries([])
    with pytest.raises(DomainError):
        series.log_progress(10)


@pytest.mark.parametrize("status", [Status.ON_HOLD, Status.COMPLETED, Status.DROPPED])
def test_logging_progress_resumes_reading(status):
    series = ReadingSeries([STARTED, StatusChanged("1", Status.READING, status)])
    events = series.log_progress(60)
    assert [type(e) for e in events] == [StatusChanged, ProgressLogged]
    assert events[0].to_status is Status.READING
    assert series.status is Status.READING


def test_lower_chapter_is_allowed():
    series = ReadingSeries([STARTED, ProgressLogged("1", 57, 1)])
    events = series.log_progress(50)
    assert events[0].previous_chapter == 57


def test_cannot_change_to_same_status():
    series = ReadingSeries([STARTED])
    with pytest.raises(DomainError):
        series.change_status(Status.READING)

def test_remove_series():
    series = ReadingSeries([STARTED])
    events = series.remove()
    assert [type(e) for e in events] == [SeriesRemoved]
    assert events[0].title == "Solo Leveling"
    assert series.removed


def test_cannot_remove_unknown_series():
    with pytest.raises(DomainError):
        ReadingSeries([]).remove()


def test_cannot_remove_twice():
    series = ReadingSeries([STARTED, SeriesRemoved("1", "Solo Leveling")])
    with pytest.raises(DomainError):
        series.remove()


def test_removed_series_takes_no_progress_or_status():
    series = ReadingSeries([STARTED, SeriesRemoved("1", "Solo Leveling")])
    with pytest.raises(DomainError):
        series.log_progress(10)
    with pytest.raises(DomainError):
        series.change_status(Status.COMPLETED)


def test_start_with_another_status():
    series = ReadingSeries([])
    events = series.start("1", "Lord of the Mysteries", Kind.NOVEL, "x", 1432, status=Status.COMPLETED)
    assert [type(e) for e in events] == [SeriesStarted, StatusChanged]
    assert events[1].from_status is Status.READING and events[1].to_status is Status.COMPLETED
    assert series.status is Status.COMPLETED


def test_start_as_reading_is_one_event():
    events = ReadingSeries([]).start("1", "Solo Leveling", Kind.MANHWA, "asura", 1, status=Status.READING)
    assert [type(e) for e in events] == [SeriesStarted]


@pytest.mark.parametrize("start, target", [
    (Status.COMPLETED, Status.READING), (Status.READING, Status.ON_HOLD),
    (Status.DROPPED, Status.COMPLETED), (Status.ON_HOLD, Status.DROPPED),
])
def test_every_status_can_change_to_every_other(start, target):
    series = ReadingSeries([STARTED, *([StatusChanged("1", Status.READING, start)] if start is not Status.READING else [])])
    assert series.change_status(target)[0].to_status is target


# ---- Genres ----

def test_set_genres():
    series = ReadingSeries([STARTED])
    [event] = series.set_genres(["Fantasy", "Action"])
    assert isinstance(event, GenresChanged) and event.genres == ["Action", "Fantasy"]
    assert series.genres == ["Action", "Fantasy"]


def test_same_genres_again_is_no_change():
    series = ReadingSeries([STARTED, GenresChanged("1", ["Action"])])
    assert series.set_genres(["Action"]) == []


def test_genres_can_be_cleared():
    series = ReadingSeries([STARTED, GenresChanged("1", ["Action"])])
    assert series.set_genres([])[0].genres == []


def test_unknown_genre_is_refused():
    with pytest.raises(DomainError):
        ReadingSeries([STARTED]).set_genres(["Ninja"])


def test_start_with_genres():
    events = ReadingSeries([]).start("1", "Solo Leveling", Kind.MANHWA, "asura", 1, genres=["Action", "Fantasy"])
    assert [type(e) for e in events] == [SeriesStarted, GenresChanged]


def test_removed_series_gets_no_genres():
    series = ReadingSeries([STARTED, SeriesRemoved("1", "Solo Leveling")])
    with pytest.raises(DomainError):
        series.set_genres(["Action"])
