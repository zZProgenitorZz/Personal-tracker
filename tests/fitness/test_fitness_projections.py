"""Fitness read models: steps per day, gym visits (short ones don't count), weeks and the streak."""
import datetime as dt

import app.clock as clock
from app.event_types import ALL_EVENT_TYPES
from app.eventstore import EventStore
from app.fitness.commands import ArriveAtGym, FitnessCommandHandler, LeaveGym, RecordSteps
from app.fitness.events import GymArrived, GymLeft, StepsRecorded
from app.fitness.projections import GymProjection, StepsProjection, week_totals

DAY = dt.date(2026, 10, 14)  # a Wednesday
NOW = dt.datetime(2026, 10, 16, 21, 0)


def at(hour, minute=0, day=DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute))


def gym_with(*events) -> GymProjection:
    gym = GymProjection()
    for event in events:
        gym.apply(event)
    return gym


def visit(day, start, end=None):
    """The events of one visit; start and end are datetimes, no end = still open."""
    return [GymArrived(day, start)] + ([GymLeft(day, end)] if end else [])


# ---- Steps ----

def test_steps_per_day_the_last_number_counts():
    steps = StepsProjection()
    for event in (StepsRecorded(DAY, 5000), StepsRecorded(DAY - dt.timedelta(days=1), 7000), StepsRecorded(DAY, 8123)):
        steps.apply(event)
    assert steps.per_day() == {DAY - dt.timedelta(days=1): 7000, DAY: 8123}
    assert steps.between(DAY, DAY) == {DAY: 8123}


# ---- Visits ----

def test_a_visit_has_start_end_and_minutes():
    [v] = gym_with(*visit(DAY, at(18), at(19, 5))).visits(NOW)
    assert (v.day, v.start, v.end, v.minutes, v.in_progress) == (DAY, at(18), at(19, 5), 65, False)


def test_a_visit_shorter_than_ten_minutes_does_not_count():
    gym = gym_with(*visit(DAY, at(18), at(18, 9)), *visit(DAY, at(19), at(19, 10)))
    assert [v.start for v in gym.visits(NOW)] == [at(19)]  # exactly 10 minutes does


def test_several_visits_on_one_day_count_separately():
    gym = gym_with(*visit(DAY, at(8), at(9)), *visit(DAY, at(18), at(19)))
    assert len(gym.visits(NOW)) == 2 and gym.per_day(NOW) == {DAY: 2}


def test_an_open_visit_after_four_hours_is_a_visit_of_unknown_duration():
    gym = gym_with(*visit(DAY, at(12)))
    [v] = gym.visits(at(16, 1))
    assert v.minutes is None and v.end is None and not v.in_progress
    assert gym.visits(at(15, 59))[0].minutes == 239  # not yet: still in progress


def test_an_open_visit_is_in_progress_and_counts_once_it_is_ten_minutes_old():
    gym = gym_with(*visit(DAY, at(18)))
    assert gym.visits(at(18, 9)) == []
    [v] = gym.visits(at(18, 40))
    assert v.in_progress and v.minutes == 40


def test_a_visit_closed_after_more_than_four_hours_is_unknown_too():
    [v] = gym_with(*visit(DAY, at(8), at(14))).visits(NOW)
    assert v.minutes is None and v.end == at(14)


def test_a_departure_after_midnight_closes_the_visit_of_the_arrival_day():
    day2 = DAY + dt.timedelta(days=1)
    gym = gym_with(*visit(DAY, at(23, 30)), GymLeft(DAY, at(0, 40, day2)))
    [v] = gym.visits(NOW)
    assert v.day == DAY and v.minutes == 70


def test_the_order_of_the_events_does_not_matter():
    in_order = gym_with(GymArrived(DAY, at(18)), GymLeft(DAY, at(19, 15)))
    departure_first = gym_with(GymLeft(DAY, at(19, 15)), GymArrived(DAY, at(18)))
    assert departure_first.visits(NOW) == in_order.visits(NOW)
    assert [v.minutes for v in departure_first.visits(NOW)] == [75]


def test_late_and_shuffled_messages_still_pair_up_per_visit():
    earlier = DAY - dt.timedelta(days=1)
    gym = gym_with(GymLeft(DAY, at(19)), GymArrived(earlier, at(18, day=earlier)), GymArrived(DAY, at(8)),
                   GymLeft(earlier, at(19, 30, day=earlier)), GymLeft(DAY, at(9)), GymArrived(DAY, at(18)))
    assert [(v.day, v.minutes) for v in gym.visits(NOW)] == [(earlier, 90), (DAY, 60), (DAY, 60)]


def test_a_departure_without_an_arrival_before_it_is_ignored():
    gym = gym_with(GymLeft(DAY, at(7)), GymArrived(DAY, at(18)), GymLeft(DAY, at(19)), GymLeft(DAY, at(20)))
    assert [(v.start, v.minutes) for v in gym.visits(NOW)] == [(at(18), 60)]


def test_a_second_arrival_during_the_same_visit_is_the_same_visit():
    gym = gym_with(GymArrived(DAY, at(18)), GymArrived(DAY, at(18, 30)), GymLeft(DAY, at(19)))
    assert [(v.start, v.minutes) for v in gym.visits(NOW)] == [(at(18), 60)]


def test_an_arrival_long_after_an_unclosed_one_is_a_new_visit():
    gym = gym_with(GymArrived(DAY, at(8)), GymArrived(DAY, at(18)), GymLeft(DAY, at(19)))
    assert [(v.start, v.minutes) for v in gym.visits(NOW)] == [(at(8), None), (at(18), 60)]


# ---- Weeks ----

def test_week_totals_count_visits_hours_and_steps():
    monday = dt.date(2026, 10, 12)
    last_week = monday - dt.timedelta(days=3)
    steps = StepsProjection()
    for day, n in ((monday, 4000), (monday + dt.timedelta(days=1), 8000), (monday - dt.timedelta(days=1), 99_999)):
        steps.apply(StepsRecorded(day, n))
    gym = gym_with(*visit(monday, at(18, day=monday), at(19, 30, day=monday)),
                   *visit(DAY, at(8), at(8, 30)),
                   *visit(last_week, at(18, day=last_week), at(19, day=last_week)))
    week = week_totals(steps, gym, monday, NOW)
    assert (week.visits, week.gym_minutes, week.gym_hours) == (2, 120, 2.0)
    assert (week.steps, week.days_with_steps, week.average_steps) == (12_000, 2, 6000)


def test_an_empty_week_has_no_average_instead_of_an_error():
    week = week_totals(StepsProjection(), GymProjection(), dt.date(2026, 10, 12), NOW)
    assert (week.visits, week.gym_hours, week.average_steps) == (0, 0, 0)


def test_unknown_durations_count_as_visits_but_add_no_hours():
    gym = gym_with(*visit(DAY, at(8)))  # never left
    week = week_totals(StepsProjection(), gym, dt.date(2026, 10, 12), NOW)
    assert (week.visits, week.gym_minutes) == (1, 0)


# ---- The streak, in weeks ----

def streak_for(*days, today=dt.date(2026, 10, 16)) -> int:
    events = [e for d in days for e in visit(d, at(18, day=d), at(19, day=d))]
    return gym_with(*events).streak_weeks(today, NOW)


def test_the_streak_counts_weeks_in_a_row():
    assert streak_for(dt.date(2026, 10, 13), dt.date(2026, 10, 6), dt.date(2026, 9, 30)) == 3


def test_this_week_without_a_visit_yet_does_not_break_the_streak():
    assert streak_for(dt.date(2026, 10, 7), dt.date(2026, 9, 30)) == 2


def test_a_week_without_visits_ends_the_streak():
    assert streak_for(dt.date(2026, 10, 13), dt.date(2026, 9, 30)) == 1
    assert streak_for(dt.date(2026, 9, 30)) == 0  # last week was empty, and this one too


def test_several_visits_in_one_week_are_one_week():
    assert streak_for(dt.date(2026, 10, 12), dt.date(2026, 10, 14), dt.date(2026, 10, 16)) == 1


# ---- Rebuilding ----

def test_a_rebuilt_read_model_is_the_same_as_the_live_one(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: NOW)
    store = EventStore(":memory:", ALL_EVENT_TYPES)
    live_steps, live_gym = StepsProjection(), GymProjection()
    store.subscribe(live_steps.apply)
    store.subscribe(live_gym.apply)
    handler = FitnessCommandHandler(store)
    for command in (RecordSteps(DAY, 5000), RecordSteps(DAY, 8123), ArriveAtGym(at(8)), LeaveGym(at(9)),
                    ArriveAtGym(at(18)), LeaveGym(at(19, 20)), ArriveAtGym(at(23, 30)),
                    RecordSteps(dt.date(2026, 10, 15), 9000)):
        handler.handle(command)

    rebuilt_steps, rebuilt_gym = StepsProjection(), GymProjection()
    for event in store.load_all():  # through the JSON in the database, like after a restart
        rebuilt_steps.apply(event)
        rebuilt_gym.apply(event)
    assert rebuilt_steps.per_day() == live_steps.per_day() == {DAY: 8123, dt.date(2026, 10, 15): 9000}
    assert rebuilt_gym.visits(NOW) == live_gym.visits(NOW)
    assert [v.minutes for v in rebuilt_gym.visits(NOW)] == [60, 80, None]
