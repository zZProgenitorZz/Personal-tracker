"""Wrapped: het jaaroverzicht, opgebouwd uit de bestaande events van alle trackers."""
from datetime import date, datetime, timedelta

from app.eventstore import EventStore
from app.listening.events import TrackPlayed
from app.main import EVENT_TYPES
from app.reading.events import GenresChanged, Kind, ProgressLogged, SeriesRemoved, SeriesStarted, Status, StatusChanged
from app.watching.events import ShowAdded, ShowGenresChanged, ShowStatusChanged, WatchKind, WatchStatus
from app.wrapped.projections import WrappedProjection


def at(y, m, d, hour=12, minute=0) -> datetime:
    """Een moment in de lokale tijdzone, zoals de projecties dagen tellen."""
    return datetime(y, m, d, hour, minute).astimezone()


def started(sid, title, when, kind=Kind.MANHWA, cover=None, chapter=0):
    return SeriesStarted(sid, title, kind, "x", chapter, at=when, cover=cover)


def read(sid, frm, to, when):
    return ProgressLogged(sid, to, frm, at=when)


def status(sid, frm, to, when):
    return StatusChanged(sid, frm, to, at=when)


def added_as(sid, title, start_status, when):
    """Zoals ReadingSeries.start: SeriesStarted en direct daarna de beginstatus."""
    return [started(sid, title, when), status(sid, Status.READING, start_status, when + timedelta(milliseconds=3))]


def play(when, track="Song", artists=("Artist",), duration_ms=180_000, ms_played=None, track_id=None):
    return TrackPlayed(when, track_id or f"spotify:track:{track}", track, list(artists), "Album", "alb",
                       duration_ms, ms_played, at=when)


def given(*events) -> WrappedProjection:
    projection = WrappedProjection()
    for event in events:
        if isinstance(event, list):
            for e in event:
                projection.apply(e)
        else:
            projection.apply(event)
    return projection


# ---- Reading ----

def test_chapters_count_only_forward_progress():
    w = given(started("1", "A", at(2025, 1, 1)),
              read("1", 0, 10, at(2025, 1, 2)),
              read("1", 10, 4, at(2025, 1, 3)),   # correctie omlaag telt niet
              read("1", 4, 9, at(2025, 1, 4)))    # 4 → 9 wel
    assert w.year(2025).chapters == 15


def test_years_follow_local_dates():
    w = given(started("1", "A", at(2024, 12, 31)),
              read("1", 0, 3, at(2024, 12, 31, 23, 30)),
              read("1", 3, 5, at(2025, 1, 1, 0, 30)))
    assert (w.year(2024).chapters, w.year(2025).chapters) == (3, 2)
    assert w.years() == [2025, 2024]


def test_top_series_keeps_titles_of_removed_series():
    w = given(started("1", "Gone", at(2025, 1, 1), cover="gone.webp"), read("1", 0, 50, at(2025, 2, 1)),
              SeriesRemoved("1", "Gone", at=at(2025, 3, 1)),
              started("2", "B", at(2025, 1, 1)), read("2", 0, 20, at(2025, 2, 1)),
              *[x for i in range(3, 8) for x in (started(str(i), f"S{i}", at(2025, 1, 1)),
                                                  read(str(i), 0, i, at(2025, 2, 1)))])
    top = w.year(2025).top_series
    assert [(t.title, n) for t, n in top] == [("Gone", 50), ("B", 20), ("S7", 7), ("S6", 6), ("S5", 5)]
    assert top[0][0].cover == "gone.webp"


def test_reading_genres_split_chapters_using_the_latest_genres():
    w = given(started("1", "A", at(2025, 1, 1)),
              GenresChanged("1", ["Romance"], at=at(2025, 1, 1)),
              read("1", 0, 10, at(2025, 2, 1)),
              GenresChanged("1", ["Action", "Fantasy"], at=at(2025, 6, 1)))  # de laatste telt
    assert w.year(2025).reading_genres == [("Action", 5), ("Fantasy", 5)]


def test_adding_as_completed_is_not_finishing():
    w = given(added_as("1", "Old favourite", Status.COMPLETED, at(2025, 3, 1)))
    review = w.year(2025)
    assert review.series_completed == 0 and review.series_started == 0


def test_real_completions_count_even_right_after_adding():
    w = given(
        # Toegevoegd als Reading en een uur later afgerond: echt.
        started("1", "A", at(2025, 3, 1, 10)), status("1", Status.READING, Status.COMPLETED, at(2025, 3, 1, 11)),
        # Van Plan to Read naar Completed: een echte wijziging.
        added_as("2", "B", Status.PLAN_TO_READ, at(2025, 3, 2)),
        status("2", Status.PLAN_TO_READ, Status.COMPLETED, at(2025, 4, 1)),
        # Serie 4 wordt afgerond direct na de SeriesStarted van serie 3: geen beginstatus, want andere serie.
        started("4", "D", at(2025, 3, 2, 9)),
        started("3", "C", at(2025, 3, 3)),
        status("4", Status.READING, Status.COMPLETED, at(2025, 3, 3)),
    )
    assert w.year(2025).series_completed == 3


def test_finishing_the_same_series_twice_in_a_year_counts_once():
    w = given(started("1", "A", at(2025, 1, 1)),
              status("1", Status.READING, Status.COMPLETED, at(2025, 2, 1)),
              status("1", Status.COMPLETED, Status.READING, at(2025, 3, 1)),
              status("1", Status.READING, Status.COMPLETED, at(2025, 4, 1)))
    assert w.year(2025).series_completed == 1


def test_plan_to_read_is_not_started_until_it_leaves_the_backlog():
    w = given(added_as("1", "Later", Status.PLAN_TO_READ, at(2024, 11, 1)),
              started("2", "Now", at(2025, 1, 5)),
              status("1", Status.PLAN_TO_READ, Status.READING, at(2025, 2, 1)),
              status("1", Status.READING, Status.PLAN_TO_READ, at(2025, 3, 1)),
              status("1", Status.PLAN_TO_READ, Status.READING, at(2025, 4, 1)))  # telt maar één keer
    assert (w.year(2024).series_started, w.year(2025).series_started) == (0, 2)
    assert w.year(2024).series_completed == 0 and w.year(2024).chapters == 0


def test_longest_streak_stays_within_the_year():
    days = [at(2024, 12, 30), at(2024, 12, 31), at(2025, 1, 1), at(2025, 1, 2), at(2025, 1, 3),
            at(2025, 5, 1), at(2025, 5, 2)]
    w = given(started("1", "A", at(2024, 12, 1)), *[read("1", i, i + 1, d) for i, d in enumerate(days)])
    assert (w.year(2024).longest_streak, w.year(2025).longest_streak) == (2, 3)


# ---- Watching ----

def show(sid, title, kind, start, when, cover=None):
    return ShowAdded(sid, title, kind, start, at=when, cover=cover)


def finish(sid, frm, when):
    return ShowStatusChanged(sid, frm, WatchStatus.COMPLETED, at=when)


def test_watching_counts_only_marking_completed():
    w = given(show("1", "Frieren", WatchKind.ANIME, WatchStatus.WATCHING, at(2025, 1, 1), cover="f.webp"),
              ShowGenresChanged("1", ["Fantasy", "Adventure"], at=at(2025, 1, 1)),
              finish("1", WatchStatus.WATCHING, at(2025, 2, 1)),
              show("2", "Dune", WatchKind.MOVIE, WatchStatus.PLAN_TO_WATCH, at(2025, 1, 1)),
              ShowGenresChanged("2", ["Sci-Fi", "Adventure"], at=at(2025, 1, 1)),
              finish("2", WatchStatus.PLAN_TO_WATCH, at(2025, 3, 1)),     # uit de backlog afgerond: telt
              show("3", "Seen before", WatchKind.SERIES, WatchStatus.COMPLETED, at(2025, 1, 1)),  # telt niet
              show("4", "Backlog", WatchKind.SERIES, WatchStatus.PLAN_TO_WATCH, at(2025, 1, 1)))  # telt niet
    review = w.year(2025)
    assert review.watched_by_kind == {WatchKind.SERIES: 0, WatchKind.ANIME: 1, WatchKind.MOVIE: 1}
    assert review.watched_total == 2
    assert review.watch_genres[0] == ("Adventure", 2)
    assert [(t.title, t.cover) for t in review.watched] == [("Frieren", "f.webp"), ("Dune", None)]


# ---- Listening ----

def test_listening_minutes_tops_and_busiest_day():
    w = given(play(at(2025, 1, 1), "One", ["A", "B"]),
              play(at(2025, 1, 1, 13), "One", ["A", "B"]),
              play(at(2025, 1, 2), "Two", ["A"], ms_played=60_000),  # echt geluisterd telt
              play(at(2026, 1, 1), "Three", ["C"]))
    review = w.year(2025)
    assert review.listen_minutes == 7
    assert review.top_artists == [("A", 3), ("B", 2)]
    assert [(t.track, t.plays) for t in review.top_tracks] == [("One", 2), ("Two", 1)]
    assert review.busiest_listening_day == (date(2025, 1, 1), 6)


# ---- Alles samen ----

def test_months_busiest_month_and_most_active_day():
    w = given(started("1", "A", at(2025, 1, 1)),
              read("1", 0, 10, at(2025, 3, 5)),
              status("1", Status.READING, Status.COMPLETED, at(2025, 3, 6)),
              play(at(2025, 7, 1)), play(at(2025, 7, 1, 14)))
    review = w.year(2025)
    assert len(review.months) == 12
    march, july = review.months[2], review.months[6]
    assert (march.chapters, march.finished, march.minutes) == (10, 1, 0)
    assert (july.chapters, july.finished, july.minutes) == (0, 0, 6)
    assert review.busiest_month.month == 3            # 10 hoofdstukken + 1 afgerond > 2 nummers
    assert review.most_active_day == (date(2025, 3, 5), 10)


def test_an_empty_year():
    review = WrappedProjection().year(2025)
    assert not review.has_data and review.busiest_month is None and review.most_active_day is None
    assert review.top_series == [] and review.busiest_listening_day is None


def test_plan_to_read_and_watch_are_not_read_or_finished():
    w = given(added_as("1", "Later", Status.PLAN_TO_READ, at(2025, 1, 1)),
              show("2", "Later too", WatchKind.MOVIE, WatchStatus.PLAN_TO_WATCH, at(2025, 1, 1)))
    review = w.year(2025)
    assert (review.chapters, review.series_completed, review.watched_total, review.finished) == (0, 0, 0, 0)


def test_rebuilt_projection_equals_the_live_one():
    store = EventStore(":memory:", EVENT_TYPES)
    live = WrappedProjection()
    store.subscribe(live.apply)
    store.append("s1", added_as("s1", "Done", Status.COMPLETED, at(2025, 1, 1)))
    store.append("s2", [started("s2", "B", at(2025, 1, 2)), GenresChanged("s2", ["Action"], at=at(2025, 1, 2))])
    store.append("play-1", [play(at(2025, 1, 3))])  # een sync tussendoor
    store.append("s2", [read("s2", 0, 12, at(2025, 2, 1)), status("s2", Status.READING, Status.COMPLETED, at(2025, 2, 2))])
    store.append("w1", [show("w1", "Dune", WatchKind.MOVIE, WatchStatus.WATCHING, at(2025, 1, 1)),
                        finish("w1", WatchStatus.WATCHING, at(2025, 5, 1))])

    rebuilt = WrappedProjection()
    for event in store.load_all():
        rebuilt.apply(event)
    assert rebuilt.years() == live.years() == [2025]
    assert rebuilt.year(2025) == live.year(2025)
    assert live.year(2025).series_completed == 1 and live.year(2025).finished == 2


# ---- Verwijderd en opnieuw toegevoegd: in Wrapped één serie ----
# Zo kregen oude series een cover: verwijderen en opnieuw toevoegen (nieuwe series_id, Reading regel 6).

def test_re_added_series_shows_once_with_the_new_cover_and_all_chapters():
    w = given(started("old", "Shadow Slave", at(2025, 1, 1)), read("old", 0, 80, at(2025, 2, 1)),
              SeriesRemoved("old", "Shadow Slave", at=at(2025, 3, 1)),
              started("new", "shadow slave", at(2025, 3, 1), cover="ss.webp", chapter=80))  # hoofdletters tellen niet
    [(title, chapters)] = w.year(2025).top_series
    assert (title.title, title.cover, chapters) == ("shadow slave", "ss.webp", 80)


def test_chapters_of_both_versions_are_added_up():
    w = given(started("old", "Solo Leveling", at(2025, 1, 1)), read("old", 0, 30, at(2025, 2, 1)),
              SeriesRemoved("old", "Solo Leveling", at=at(2025, 3, 1)),
              started("new", "Solo Leveling", at(2025, 3, 1), cover="sl.webp", chapter=30),
              read("new", 30, 45, at(2025, 4, 1)),
              started("other", "B", at(2025, 1, 1)), read("other", 0, 40, at(2025, 2, 1)))
    assert [(t.title, t.cover, n) for t, n in w.year(2025).top_series] == [
        ("Solo Leveling", "sl.webp", 45), ("B", None, 40)]


def test_an_older_cover_is_used_when_the_newest_version_has_none():
    w = given(started("v1", "A", at(2025, 1, 1), cover="a1.webp"), read("v1", 0, 5, at(2025, 1, 2)),
              SeriesRemoved("v1", "A", at=at(2025, 1, 3)),
              started("v2", "A", at(2025, 1, 4)))
    assert w.year(2025).top_series[0][0].cover == "a1.webp"


def test_genres_of_the_new_version_count_for_the_old_chapters():
    w = given(started("old", "A", at(2025, 1, 1)), read("old", 0, 10, at(2025, 2, 1)),
              SeriesRemoved("old", "A", at=at(2025, 3, 1)),
              started("new", "A", at(2025, 3, 1), chapter=10),
              GenresChanged("new", ["Fantasy"], at=at(2025, 3, 1)))
    assert w.year(2025).reading_genres == [("Fantasy", 10)]


def test_adding_again_is_not_starting_again():
    w = given(started("old", "A", at(2024, 6, 1)), read("old", 0, 10, at(2024, 7, 1)),
              SeriesRemoved("old", "A", at=at(2025, 3, 1)),
              started("new", "A", at(2025, 3, 1), chapter=10),
              # ook niet als de nieuwe versie als Completed wordt toegevoegd en daarna weer Reading wordt
              SeriesRemoved("new", "A", at=at(2025, 4, 1)),
              added_as("newer", "A", Status.PLAN_TO_READ, at(2025, 4, 1)),
              status("newer", Status.PLAN_TO_READ, Status.READING, at(2025, 5, 1)))
    assert (w.year(2024).series_started, w.year(2025).series_started) == (1, 0)


def test_re_added_as_completed_does_not_undo_the_first_start():
    w = given(started("old", "A", at(2025, 1, 1)),
              SeriesRemoved("old", "A", at=at(2025, 2, 1)),
              added_as("new", "A", Status.COMPLETED, at(2025, 2, 1)))
    assert (w.year(2025).series_started, w.year(2025).series_completed) == (1, 0)


def test_rebuilt_equals_live_with_re_added_series():
    store = EventStore(":memory:", EVENT_TYPES)
    live = WrappedProjection()
    store.subscribe(live.apply)
    store.append("old", [started("old", "Shadow Slave", at(2025, 1, 1)), read("old", 0, 50, at(2025, 2, 1))])
    store.append("old", [SeriesRemoved("old", "Shadow Slave", at=at(2025, 3, 1))])
    store.append("new", [started("new", "Shadow Slave", at(2025, 3, 1), cover="ss.webp", chapter=50),
                         GenresChanged("new", ["Fantasy"], at=at(2025, 3, 1))])
    store.append("new", [read("new", 50, 60, at(2025, 4, 1))])

    rebuilt = WrappedProjection()
    for event in store.load_all():
        rebuilt.apply(event)
    assert rebuilt.year(2025) == live.year(2025)
    [(title, chapters)] = live.year(2025).top_series
    assert (title.cover, chapters, live.year(2025).reading_genres) == ("ss.webp", 60, [("Fantasy", 60)])
