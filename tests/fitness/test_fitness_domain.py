"""Fitness rules: given events, when a command, then new events or a DomainError. Fixed clock."""
import datetime as dt

import pytest

import app.clock as clock
from app.domain import DomainError
from app.event_types import ALL_EVENT_TYPES
from app.eventstore import EventStore
from app.fitness.aggregate import MAX_STEPS, GymDay, StepsDay
from app.fitness.commands import ArriveAtGym, FitnessCommandHandler, LeaveGym, RecordSteps
from app.fitness.events import GymArrived, GymLeft, StepsRecorded

DAY = dt.date(2026, 10, 16)
NOW = dt.datetime(2026, 10, 16, 21, 0)  # a Friday evening


def at(hour, minute=0, day=DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute))


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: NOW)
    store = EventStore(":memory:", ALL_EVENT_TYPES)
    handler = FitnessCommandHandler(store)
    handler.store = store
    return handler


# ---- Steps ----

def test_steps_for_a_day_are_recorded():
    [event] = StepsDay(DAY, []).record(8123, DAY)
    assert (event.day, event.steps) == (DAY, 8123)


def test_the_same_steps_again_is_no_event():
    assert StepsDay(DAY, [StepsRecorded(DAY, 8123)]).record(8123, DAY) == []


def test_other_steps_are_a_new_event_and_the_last_one_counts():
    day = StepsDay(DAY, [StepsRecorded(DAY, 5000)])
    [event] = day.record(8123, DAY)
    assert event.steps == 8123 and day.steps == 8123
    assert day.record(5000, DAY)[0].steps == 5000  # going back down is also a change


@pytest.mark.parametrize("steps", [-1, MAX_STEPS + 1])
def test_negative_or_absurd_steps_are_refused(steps):
    with pytest.raises(DomainError, match="between 0 and 200,000"):
        StepsDay(DAY, []).record(steps, DAY)


@pytest.mark.parametrize("steps", [0, MAX_STEPS])
def test_the_edges_are_fine(steps):
    assert len(StepsDay(DAY, []).record(steps, DAY)) == 1


def test_steps_for_a_day_that_has_not_happened_yet_are_refused():
    with pytest.raises(DomainError, match="future"):
        StepsDay(DAY + dt.timedelta(days=1), []).record(100, DAY)


def test_steps_stream_is_per_day():
    assert StepsDay(DAY, []).stream_id == "steps-2026-10-16"


# ---- Gym ----

def test_arriving_records_the_day_and_time():
    [event] = GymDay(DAY, []).arrive(at(18, 30), NOW)
    assert (event.day, event.arrived_at) == (DAY, at(18, 30))


def test_leaving_records_the_day_and_time():
    [event] = GymDay(DAY, []).leave(at(19, 15), NOW)
    assert (event.day, event.left_at) == (DAY, at(19, 15))


def test_a_time_within_a_minute_of_a_known_one_is_the_same_message_again():
    gym = GymDay(DAY, [GymArrived(DAY, at(18)), GymLeft(DAY, at(19))])
    assert gym.arrive(at(18), NOW) == [] and gym.arrive(at(18, 1), NOW) == []
    assert gym.leave(at(19), NOW) == [] and gym.leave(at(18, 59), NOW) == []
    assert len(gym.arrive(at(18, 2), NOW)) == 1  # more than a minute away: another arrival
    assert len(gym.leave(at(19, 2), NOW)) == 1


def test_an_arrival_and_a_departure_at_the_same_time_are_not_each_others_duplicate():
    gym = GymDay(DAY, [GymArrived(DAY, at(18))])
    assert len(gym.leave(at(18), NOW)) == 1


def test_any_order_is_fine():
    gym = GymDay(DAY, [])
    assert len(gym.leave(at(19), NOW)) == 1  # the departure arrives before its arrival
    assert len(gym.arrive(at(18), NOW)) == 1
    assert len(gym.arrive(at(8), NOW)) == 1 and len(gym.arrive(at(7), NOW)) == 1  # also several, out of order


def test_a_moment_in_the_future_is_refused_but_a_phone_clock_a_little_ahead_is_fine():
    with pytest.raises(DomainError, match="future"):
        GymDay(DAY, []).arrive(NOW + dt.timedelta(minutes=6), NOW)
    with pytest.raises(DomainError, match="future"):
        GymDay(DAY, []).leave(NOW + dt.timedelta(minutes=6), NOW)
    assert len(GymDay(DAY, []).arrive(NOW + dt.timedelta(minutes=5), NOW)) == 1


# ---- The handler: load, decide, save ----

def test_the_same_steps_twice_make_one_event(handler):
    assert len(handler.handle(RecordSteps(DAY, 8123))) == 1
    assert handler.handle(RecordSteps(DAY, 8123)) == []
    assert handler.store.count() == 1


def test_the_same_arrival_twice_makes_one_event(handler):
    handler.handle(ArriveAtGym(at(18)))
    assert handler.handle(ArriveAtGym(at(18))) == []
    assert handler.handle(ArriveAtGym(at(18, 1))) == []  # sent again with a slightly different time
    assert handler.store.count() == 1


def test_the_same_departure_twice_makes_one_event(handler):
    assert len(handler.handle(LeaveGym(at(19)))) == 1
    assert handler.handle(LeaveGym(at(19))) == []
    assert handler.store.count() == 1


def test_a_departure_is_saved_without_an_arrival_and_before_it(handler):
    handler.handle(LeaveGym(at(19)))
    handler.handle(ArriveAtGym(at(18)))
    assert [type(e) for e in handler.store.load_stream("gym-2026-10-16")] == [GymLeft, GymArrived]


def test_messages_land_in_the_stream_of_the_day_of_their_own_time(handler, monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: dt.datetime(2026, 10, 17, 9, 0))
    handler.handle(ArriveAtGym(at(23, 30)))                                    # yesterday evening, sent late
    handler.handle(LeaveGym(dt.datetime(2026, 10, 17, 0, 40)))                 # after midnight
    assert [e.arrived_at for e in handler.store.load_stream("gym-2026-10-16")] == [at(23, 30)]
    assert [e.left_at for e in handler.store.load_stream("gym-2026-10-17")] == [dt.datetime(2026, 10, 17, 0, 40)]


def test_a_batch_is_all_or_nothing(handler):
    with pytest.raises(DomainError):
        handler.handle_many([RecordSteps(DAY, 5000), RecordSteps(DAY - dt.timedelta(days=1), -5)])
    assert handler.store.count() == 0
    with pytest.raises(DomainError):
        handler.handle_many([ArriveAtGym(at(18)), LeaveGym(NOW + dt.timedelta(hours=1))])
    assert handler.store.count() == 0


def test_in_a_batch_the_last_number_for_a_day_counts(handler):
    results = handler.handle_many([RecordSteps(DAY, 5000), RecordSteps(DAY, 6000), RecordSteps(DAY, 6000)])
    assert [len(events) for events in results] == [1, 1, 0]
    assert StepsDay(DAY, handler.store.load_stream("steps-2026-10-16")).steps == 6000


def test_an_unknown_command_is_a_type_error(handler):
    with pytest.raises(TypeError):
        handler.handle(object())
