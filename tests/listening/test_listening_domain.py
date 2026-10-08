"""Listening: Spotify-luistergeschiedenis. Elke play is een eigen stream, op tijdstip."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain import DomainError
from app.eventstore import EventStore
from app.listening.aggregate import Play
from app.listening.commands import ListeningCommandHandler, RecordPlay, stream_id
from app.listening.events import TrackPlayed

AT = datetime(2026, 10, 8, 12, 30, 15, 123000, tzinfo=timezone.utc)


def record(played_at=AT, **fields) -> RecordPlay:
    values = dict(track_id="spotify:track:abc", track="Blinding Lights", artists=["The Weeknd"],
                  album="After Hours", album_id="alb1", duration_ms=200_000)
    return RecordPlay(played_at, **{**values, **fields})


def make_handler():
    store = EventStore(":memory:", [TrackPlayed])
    return ListeningCommandHandler(store), store


# ---- Stream-id ----

def test_stream_id_is_the_time_in_utc():
    assert stream_id(AT) == "play-2026-10-08T12:30:15.123000+00:00"
    amsterdam = timezone(timedelta(hours=2))
    assert stream_id(AT.astimezone(amsterdam)) == stream_id(AT)


def test_time_without_timezone_counts_as_utc():
    assert stream_id(AT.replace(tzinfo=None)) == stream_id(AT)


# ---- Aggregate ----

def test_first_play_at_a_moment_is_recorded():
    [event] = Play([]).record(record())
    assert isinstance(event, TrackPlayed)
    assert (event.played_at, event.track_id, event.source, event.ms_played) == (AT, "spotify:track:abc", "api", None)


def test_play_at_an_existing_moment_is_skipped_without_error():
    existing = Play([]).record(record())
    assert Play(existing).record(record()) == []


@pytest.mark.parametrize("fields", [
    {"track_id": ""}, {"track_id": "   "}, {"duration_ms": -1}, {"ms_played": -5},
])
def test_invalid_play_is_refused(fields):
    with pytest.raises(DomainError):
        Play([]).record(record(**fields))


def test_plays_have_no_way_to_be_removed():
    assert not hasattr(Play([]), "remove")


# ---- Commands ----

def test_handler_stores_a_play_in_its_own_stream():
    handler, store = make_handler()
    [event] = handler.handle(record())
    assert store.load_stream(stream_id(AT)) == [event]


def test_handler_says_nothing_was_stored_for_a_duplicate():
    handler, store = make_handler()
    handler.handle(record())
    assert handler.handle(record(track="Iets anders")) == []
    assert len(store.load_all()) == 1


def test_plays_at_different_moments_are_both_stored():
    handler, store = make_handler()
    handler.handle(record())
    handler.handle(record(played_at=AT + timedelta(minutes=4)))
    assert len(store.load_all()) == 2


def test_event_survives_storage():
    handler, store = make_handler()
    handler.handle(record(ms_played=12_000, artists=["A", "B"]))
    [event] = store.load_all()
    assert event.played_at == AT and event.artists == ["A", "B"] and event.ms_played == 12_000


def test_event_class_names_are_unique_across_domains():
    from app.main import EVENT_TYPES
    names = [t.__name__ for t in EVENT_TYPES]
    assert len(names) == len(set(names)) and "TrackPlayed" in names
