"""Reading history: what was read and when, removed series included. Built from existing events only."""
from datetime import date, datetime, timedelta

from app.reading.events import Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged
from app.reading.projections import ReadingActivityProjection, ReadingHistoryProjection
from app.wrapped.projections import WrappedProjection


def at(y, m, d, hour=12, minute=0) -> datetime:
    """A moment in the local time zone, the way the projections count days."""
    return datetime(y, m, d, hour, minute).astimezone()


def started(sid, title, when, chapter=0, kind=Kind.MANHWA):
    return SeriesStarted(sid, title, kind, "x", chapter, at=when)


def read(sid, frm, to, when):
    return ProgressLogged(sid, to, frm, at=when)


def status(sid, frm, to, when):
    return StatusChanged(sid, frm, to, at=when)


def given(*events, projection=None):
    projection = projection or ReadingHistoryProjection()
    for event in events:
        projection.apply(event)
    return projection


# ---- One rule for chapters read: corrections on the same day ----

def chapters_per_day(*events):
    """The same events through all three users of the rule: the numbers must match."""
    events = (started("1", "A", at(2026, 1, 1)), started("2", "B", at(2026, 1, 1)), *events)
    activity, history, wrapped = (given(*events, projection=p) for p in
                                  (ReadingActivityProjection(), ReadingHistoryProjection(), WrappedProjection()))
    per_day = activity.per_day()
    assert sum(per_day.values()) == history.between(date(2000, 1, 1), date(2100, 1, 1)).chapters
    assert sum(per_day.values()) == sum(wrapped.year(y).chapters for y in {d.year for d in per_day} or {2026})
    return per_day


def test_a_typo_corrected_on_the_same_day_counts_nothing():
    assert chapters_per_day(read("1", 12, 120, at(2026, 9, 1, 10)), read("1", 120, 12, at(2026, 9, 1, 11))) == {}


def test_a_partial_correction_on_the_same_day_keeps_the_rest():
    assert chapters_per_day(read("1", 12, 20, at(2026, 9, 1, 10)),
                            read("1", 20, 15, at(2026, 9, 1, 11))) == {date(2026, 9, 1): 3}


def test_a_step_down_on_a_later_day_is_a_reread_and_keeps_the_earlier_day():
    assert chapters_per_day(read("1", 12, 20, at(2026, 9, 1)),
                            read("1", 20, 15, at(2026, 9, 2))) == {date(2026, 9, 1): 8}


def test_a_correction_only_takes_back_that_series_chapters():
    per_day = chapters_per_day(read("1", 0, 10, at(2026, 9, 1, 9)), read("2", 0, 5, at(2026, 9, 1, 10)),
                               read("2", 5, 0, at(2026, 9, 1, 11)))  # 5 → 0 takes back 5, not 5 of series 1
    assert per_day == {date(2026, 9, 1): 10}


# ---- History ----

def test_a_removed_and_re_added_series_is_one_timeline():
    h = given(started("old", "Solo Leveling", at(2026, 3, 1)),
              read("old", 0, 40, at(2026, 3, 2)),
              SeriesRemoved("old", "Solo Leveling", at=at(2026, 6, 1)),
              started("new", "solo leveling ", at(2026, 6, 1, 13)),  # re-added for a cover
              read("new", 40, 60, at(2026, 6, 2)))
    timeline = h.timeline(h.key_of("old"))
    assert h.key_of("new") == h.key_of("old")
    assert timeline.title == "solo leveling "  # the newest version's title
    assert (timeline.added, timeline.chapters) == (date(2026, 3, 1), 60)
    assert (timeline.first_read, timeline.last_read) == (date(2026, 3, 2), date(2026, 6, 2))


def test_history_between_september_gives_chapters_ranges_and_a_completed_change():
    h = given(started("1", "Omniscient Reader", at(2026, 8, 1), chapter=100),
              read("1", 100, 110, at(2026, 8, 31)),
              read("1", 110, 130, at(2026, 9, 2)),
              read("1", 130, 140, at(2026, 9, 14)),
              status("1", Status.READING, Status.COMPLETED, at(2026, 9, 14, 13)),
              started("2", "Tower of God", at(2026, 9, 5), chapter=0),
              read("2", 0, 5, at(2026, 9, 6)),
              read("2", 5, 7, at(2026, 10, 1)))
    period = h.between(date(2026, 9, 1), date(2026, 9, 30))
    assert [(r.title, r.chapters, r.from_chapter, r.to_chapter) for r in period.rows] == [
        ("Omniscient Reader", 30, 110, 140), ("Tower of God", 5, 0, 5)]
    assert period.rows[0].changes == [(date(2026, 9, 14), Status.READING, Status.COMPLETED)]
    assert (period.chapters, period.days_read, period.completed) == (35, 3, 1)


def test_the_starting_status_from_adding_is_not_a_status_change():
    added = at(2026, 9, 1)
    h = given(started("1", "Shadow Slave", added),
              status("1", Status.READING, Status.PLAN_TO_READ, added + timedelta(milliseconds=3)),
              status("1", Status.PLAN_TO_READ, Status.READING, at(2026, 9, 10)))
    timeline = h.timeline(h.key_of("1"))
    assert timeline.start_status is Status.PLAN_TO_READ
    assert timeline.changes == [(date(2026, 9, 10), Status.PLAN_TO_READ, Status.READING)]
    assert timeline.times_completed == 0


def test_completed_more_than_once_is_counted():
    h = given(started("1", "A", at(2026, 1, 1)),
              status("1", Status.READING, Status.COMPLETED, at(2026, 2, 1)),
              status("1", Status.COMPLETED, Status.READING, at(2026, 5, 1)),  # rereading
              status("1", Status.READING, Status.COMPLETED, at(2026, 6, 1)))
    assert h.timeline(h.key_of("1")).times_completed == 2
