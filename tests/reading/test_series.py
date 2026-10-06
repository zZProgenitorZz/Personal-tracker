import pytest

from app.reading.aggregate import DomainError, ReadingSeries
from app.reading.events import Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged

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
