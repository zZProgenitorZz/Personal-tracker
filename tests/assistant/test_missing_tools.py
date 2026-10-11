"""De stukken die Sunny nodig had ("Tools still missing" in IDENTITY.md): elk met tests."""
import datetime as dt
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.listening.events import TrackPlayed
from app.listening.projections import TopArtistsProjection, TopTracksProjection
from app.main import create_app
from app.planner.events import Frequency, PlanAdded, PlanSkipped, Repeat
from app.planner.projections import AgendaProjection
from app.watching.aggregate import WatchItem
from app.watching.events import ShowAdded, ShowStatusChanged, WatchKind, WatchStatus
from app.watching.projections import WatchActivityProjection, WatchlistProjection


def play(day, track="Song", artists=("A",), track_id=None):
    when = datetime(*day, 12, tzinfo=timezone.utc)
    return TrackPlayed(when, track_id or f"spotify:track:{track}", track, list(artists), "Album", "a", 180_000, at=when)


# ---- Top nummers en artiesten over een vrije periode ----

def test_top_tracks_and_artists_between_two_dates():
    tracks, artists = TopTracksProjection(), TopArtistsProjection()
    for event in [play((2026, 8, 31), "Before"), play((2026, 9, 1), "One", ["A", "B"]), play((2026, 9, 20), "One", ["A", "B"]),
                  play((2026, 10, 15), "Two", ["B"]), play((2026, 10, 16), "After", ["C"])]:
        tracks.apply(event)
        artists.apply(event)
    top = tracks.between(dt.date(2026, 9, 1), dt.date(2026, 10, 15), limit=5)
    assert [(t.track, t.plays, t.minutes) for t in top] == [("One", 2, 6.0), ("Two", 1, 3.0)]
    assert artists.between(dt.date(2026, 9, 1), dt.date(2026, 10, 15), limit=5) == [("B", 3), ("A", 2)]
    assert tracks.top(2026, 9) == tracks.between(dt.date(2026, 9, 1), dt.date(2026, 9, 30))  # per maand werkt nog


def test_listening_api_takes_from_and_to_and_month_still_works():
    client = TestClient(create_app(":memory:"), headers={"Content-Type": "application/json"})
    for day in ["2026-09-01T10:00:00Z", "2026-09-02T10:00:00Z", "2026-10-15T10:00:00Z"]:
        client.post("/listening/plays", json={"played_at": day, "track_id": "spotify:track:x", "track": "X",
                                              "artists": ["A"], "album": "Al", "album_id": "al", "duration_ms": 180_000})
    period = {"from": "2026-09-01", "to": "2026-10-15"}
    assert client.get("/listening/top-tracks", params=period).json()[0]["plays"] == 3
    assert client.get("/listening/top-artists", params=period).json() == [{"artist": "A", "plays": 3}]
    assert client.get("/listening/top-artists", params={"month": "2026-09"}).json() == [{"artist": "A", "plays": 2}]
    assert client.get("/listening/top-tracks", params={"from": "2026-10-16", "to": "2026-10-01"}).status_code == 422


# ---- Rewatching ----

def test_rewatching_is_a_status_and_counts_as_watching():
    item = WatchItem([ShowAdded("1", "Frieren", WatchKind.ANIME, WatchStatus.COMPLETED)])
    [event] = item.change_status(WatchStatus.REWATCHING)
    watchlist, activity = WatchlistProjection(), WatchActivityProjection()
    for e in [ShowAdded("1", "Frieren", WatchKind.ANIME, WatchStatus.COMPLETED), event]:
        watchlist.apply(e)
        activity.apply(e)
    assert [e.title for e in watchlist.currently_watching()] == ["Frieren"]
    activity.apply(ShowStatusChanged("1", WatchStatus.REWATCHING, WatchStatus.COMPLETED))
    assert sum(activity.per_day().values()) == 1  # nog eens uitgekeken telt als afgerond


def test_old_events_still_work():
    assert WatchStatus("watching") is WatchStatus.WATCHING and WatchStatus("rewatching") is WatchStatus.REWATCHING


def test_rewatching_shows_everywhere_statuses_are_shown():
    client = TestClient(create_app(":memory:"), headers={"HX-Request": "true"})
    client.post("/ui/watching/shows", data={"title": "Frieren", "kind": "anime", "status": "rewatching"})
    assert 'value="rewatching"' in client.get("/ui/watching/add-form").text
    assert 'value="rewatching"' in client.get("/ui/watching/library").text          # filter
    grid = client.get("/ui/watching/library/grid").text
    assert "pill-rewatching" in grid and "Rewatching" in grid                    # badge en statusmenu
    css = client.get("/static/trackly.css").text
    assert ".pill-rewatching" in css and ".segmented-6" in css


# ---- Plannen in een periode ----

def test_agenda_between_lists_every_time_in_a_period():
    gym = Repeat(Frequency.WEEKLY, (0, 3))
    agenda = AgendaProjection()
    for event in [PlanAdded("1", "Gym", dt.date(2026, 10, 1), dt.time(18), None, "", gym),
                  PlanSkipped("1", dt.date(2026, 10, 15)),
                  PlanAdded("2", "Dentist", dt.date(2026, 10, 14), dt.time(9), None, "", None),
                  PlanAdded("3", "Later", dt.date(2026, 11, 2), None, None, "", None),
                  PlanAdded("4", "Someday", None, None, None, "", None)]:
        agenda.apply(event)
    found = agenda.between(dt.date(2026, 10, 12), dt.date(2026, 10, 19))
    assert [(o.title, o.on.day) for o in found] == [("Gym", 12), ("Dentist", 14), ("Gym", 19)]


def test_agenda_between_refuses_a_backwards_period():
    with pytest.raises(ValueError):
        AgendaProjection().between(dt.date(2026, 10, 19), dt.date(2026, 10, 12))
