"""Read models van Listening: recent, minuten per dag/week, top artiesten en nummers per maand."""
from datetime import date, datetime, time, timedelta, timezone

from app.eventstore import EventStore
from app.listening.commands import ListeningCommandHandler, RecordPlay
from app.listening.events import TrackPlayed
from app.listening.projections import (
    RECENT_LIMIT, ListeningActivityProjection, RecentlyPlayedProjection, TopArtistsProjection, TopTracksProjection,
)


def local(day: date, hour=12, minute=0) -> datetime:
    """Een tijdstip op `day` in de lokale tijd, als UTC (zo komt het binnen)."""
    return datetime.combine(day, time(hour, minute)).astimezone().astimezone(timezone.utc)


def play(at, track="Song", artists=("Artist",), duration_ms=180_000, ms_played=None, track_id=None):
    return RecordPlay(at, track_id or f"spotify:track:{track}", track, list(artists), "Album", "alb", duration_ms, ms_played)


def make_app():
    store = EventStore(":memory:", [TrackPlayed])
    models = [RecentlyPlayedProjection(), ListeningActivityProjection(), TopArtistsProjection(), TopTracksProjection()]
    for model in models:
        store.subscribe(model.apply)
    return ListeningCommandHandler(store), store, models


DAY = date(2026, 10, 8)


# ---- RecentlyPlayed ----

def test_recent_plays_newest_first_and_capped():
    handler, _, (recent, *_) = make_app()
    for i in range(RECENT_LIMIT + 5):
        handler.handle(play(local(DAY) + timedelta(minutes=4 * i), track=f"t{i}"))
    plays = recent.recent()
    assert len(plays) == RECENT_LIMIT
    assert plays[0].track == f"t{RECENT_LIMIT + 4}"
    assert recent.latest_played_at() == local(DAY) + timedelta(minutes=4 * (RECENT_LIMIT + 4))


def test_recent_handles_plays_arriving_out_of_order():
    handler, _, (recent, *_) = make_app()
    handler.handle(play(local(DAY, 14), track="later"))
    handler.handle(play(local(DAY, 9), track="earlier"))
    assert [p.track for p in recent.recent()] == ["later", "earlier"]


def test_no_plays_means_no_latest_time():
    assert RecentlyPlayedProjection().latest_played_at() is None


# ---- ListeningActivity ----

def test_minutes_per_day_use_ms_played_when_known():
    handler, _, (_, activity, *_) = make_app()
    handler.handle(play(local(DAY, 10), duration_ms=240_000))                # 4 min (lengte)
    handler.handle(play(local(DAY, 11), duration_ms=240_000, ms_played=30_000))  # 0,5 min (echt geluisterd)
    assert activity.minutes_per_day() == {DAY: 4.5}


def test_minutes_per_week():
    handler, _, (_, activity, *_) = make_app()
    handler.handle(play(local(date(2026, 10, 5)), duration_ms=60_000))   # maandag, week 41
    handler.handle(play(local(date(2026, 10, 11)), duration_ms=120_000))  # zondag, week 41
    handler.handle(play(local(date(2026, 10, 12)), duration_ms=60_000))  # maandag, week 42
    assert activity.minutes_per_week() == {"2026-W41": 3.0, "2026-W42": 1.0}


def test_days_follow_local_time_not_utc():
    handler, _, (_, activity, *_) = make_app()
    handler.handle(play(local(DAY, 0, 30)))  # net na middernacht lokaal
    assert list(activity.minutes_per_day()) == [DAY]


# ---- Top artiesten en nummers per maand ----

def test_top_artists_per_month_count_every_artist_of_a_play():
    handler, _, (_, _, artists, _) = make_app()
    handler.handle(play(local(DAY, 10), artists=["A", "B"]))
    handler.handle(play(local(DAY, 11), artists=["A"]))
    handler.handle(play(local(date(2026, 9, 30)), artists=["C"]))
    assert artists.top(2026, 10) == [("A", 2), ("B", 1)]
    assert artists.top(2026, 9) == [("C", 1)]
    assert artists.top(2026, 8) == []


def test_top_tracks_per_month_by_track_id():
    handler, _, (*_, tracks) = make_app()
    for hour in (9, 10, 11):
        handler.handle(play(local(DAY, hour), track="Hit", track_id="spotify:track:hit"))
    handler.handle(play(local(DAY, 12), track="Other"))
    top = tracks.top(2026, 10, limit=1)
    assert [(t.track, t.plays) for t in top] == [("Hit", 3)]
    assert top[0].artists == ["Artist"]


def test_ties_are_broken_by_name():
    handler, _, (_, _, artists, _) = make_app()
    handler.handle(play(local(DAY, 10), artists=["Zed"]))
    handler.handle(play(local(DAY, 11), artists=["Abba"]))
    assert artists.top(2026, 10) == [("Abba", 1), ("Zed", 1)]


# ---- Opnieuw opbouwen ----

def test_rebuilt_read_models_equal_the_live_ones():
    handler, store, live = make_app()
    for i in range(8):
        handler.handle(play(local(DAY - timedelta(days=i), 20), track=f"t{i % 3}", artists=[f"a{i % 2}"],
                            ms_played=60_000 * i or None))
    rebuilt = [type(m)() for m in live]
    for event in store.load_all():
        for model in rebuilt:
            model.apply(event)
    assert rebuilt[0].recent() == live[0].recent()
    assert rebuilt[1].minutes_per_day() == live[1].minutes_per_day()
    assert rebuilt[1].minutes_per_week() == live[1].minutes_per_week()
    assert rebuilt[2].top(2026, 10) == live[2].top(2026, 10)
    assert rebuilt[3].top(2026, 10) == live[3].top(2026, 10)


def test_reset_empties_every_read_model():
    handler, _, models = make_app()
    handler.handle(play(local(DAY)))
    for model in models:
        model.reset()
    assert models[0].recent() == [] and models[1].minutes_per_day() == {}
    assert models[2].top(2026, 10) == [] and models[3].top(2026, 10) == []
