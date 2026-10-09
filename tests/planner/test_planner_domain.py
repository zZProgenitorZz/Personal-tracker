"""Planner: afspraken en plannen. Regels als given/when/then, zonder netwerk of klok."""
import datetime as dt

import pytest

from app.domain import DomainError
from app.eventstore import EventStore
from app.planner.aggregate import Plan, ReminderSettings
from app.planner.commands import (
    AddPlan, ChangeReminderSettings, EditPlan, MarkPlanDone, PlannerCommandHandler, RemovePlan, ReopenPlan,
    ReschedulePlan, SkipPlan,
)
from app.planner.events import (
    EVENT_TYPES, Frequency, PlanAdded, PlanDone, PlanEdited, PlanRemoved, PlanReopened, PlanRescheduled,
    PlanSkipped, ReminderSettingsChanged, Repeat,
)
from app.planner.schedule import occurs_on

MON = dt.date(2026, 10, 12)          # maandag
DENTIST = PlanAdded("1", "Dentist", MON, dt.time(14), None, "", None)
SOMEDAY = PlanAdded("2", "Paint the hall", None, None, None, "", None)
GYM = PlanAdded("3", "Gym", MON, dt.time(7), 60, "", Repeat(Frequency.WEEKLY, (0, 3)))  # ma en do


# ---- Toevoegen ----

def test_add_a_plan():
    events = Plan([]).add("1", "  Dentist ", MON, dt.time(14), 30, "Bring card", None)
    assert events == [PlanAdded("1", "Dentist", MON, dt.time(14), 30, "Bring card", None, at=events[0].at)]


def test_add_for_someday():
    [event] = Plan([]).add("2", "Paint the hall", None, None, None, "", None)
    assert event.day is None and event.time is None


@pytest.mark.parametrize("title", ["", "   "])
def test_title_may_not_be_empty(title):
    with pytest.raises(DomainError):
        Plan([]).add("1", title, MON, None, None, "", None)


def test_a_time_without_a_date_is_refused():
    with pytest.raises(DomainError):
        Plan([]).add("1", "Call mum", None, dt.time(18), None, "", None)


def test_a_repeat_needs_a_date():
    with pytest.raises(DomainError):
        Plan([]).add("1", "Gym", None, None, None, "", Repeat(Frequency.DAILY))


def test_weekly_without_weekdays_repeats_on_the_weekday_of_its_date():
    [event] = Plan([]).add("1", "Gym", MON, None, None, "", Repeat(Frequency.WEEKLY))
    assert event.repeat.weekdays == (0,)


def test_repeat_may_not_end_before_it_starts():
    with pytest.raises(DomainError):
        Plan([]).add("1", "Gym", MON, None, None, "", Repeat(Frequency.DAILY, until=MON - dt.timedelta(days=1)))


# ---- Herhaling ----

def test_daily_weekly_and_until():
    daily = Repeat(Frequency.DAILY, until=MON + dt.timedelta(days=2))
    assert [occurs_on(MON, daily, MON + dt.timedelta(days=i)) for i in range(-1, 4)] == [False, True, True, True, False]
    weekly = Repeat(Frequency.WEEKLY, (0, 3))
    assert [d for d in range(14) if occurs_on(MON, weekly, MON + dt.timedelta(days=d))] == [0, 3, 7, 10]


def test_monthly_on_the_31st_falls_on_the_last_day_of_short_months():
    start = dt.date(2026, 1, 31)
    monthly = Repeat(Frequency.MONTHLY)
    assert occurs_on(start, monthly, dt.date(2026, 2, 28))
    assert occurs_on(start, monthly, dt.date(2026, 4, 30))
    assert not occurs_on(start, monthly, dt.date(2026, 4, 29))
    assert occurs_on(start, monthly, dt.date(2026, 5, 31))


def test_without_repeat_only_its_own_day():
    assert occurs_on(MON, None, MON) and not occurs_on(MON, None, MON + dt.timedelta(days=7))


# ---- Afvinken, heropenen en overslaan ----

def test_mark_done_and_reopen():
    plan = Plan([DENTIST])
    [done] = plan.mark_done(MON)
    assert isinstance(done, PlanDone) and done.on == MON
    [reopened] = plan.reopen(MON)
    assert isinstance(reopened, PlanReopened)


def test_marking_done_twice_is_refused():
    with pytest.raises(DomainError):
        Plan([DENTIST, PlanDone("1", MON)]).mark_done(MON)


def test_reopening_what_is_not_done_is_refused():
    with pytest.raises(DomainError):
        Plan([DENTIST]).reopen(MON)


def test_each_occurrence_of_a_repeat_is_done_separately():
    plan = Plan([GYM, PlanDone("3", MON)])
    assert plan.mark_done(MON + dt.timedelta(days=3))  # donderdag: los afvinken
    with pytest.raises(DomainError):
        plan.mark_done(MON)


@pytest.mark.parametrize("action", ["mark_done", "skip"])
def test_done_or_skipped_on_a_day_the_plan_does_not_fall_is_refused(action):
    with pytest.raises(DomainError):
        getattr(Plan([GYM]), action)(MON + dt.timedelta(days=1))  # dinsdag


def test_a_single_plan_is_done_on_its_own_day_only():
    with pytest.raises(DomainError):
        Plan([DENTIST]).mark_done(MON + dt.timedelta(days=1))


def test_a_someday_plan_is_done_on_the_day_you_tick_it():
    [done] = Plan([SOMEDAY]).mark_done(dt.date(2026, 11, 3))
    assert done.on == dt.date(2026, 11, 3)
    with pytest.raises(DomainError):
        Plan([SOMEDAY, done]).mark_done(dt.date(2026, 11, 4))


def test_skip_one_occurrence():
    plan = Plan([GYM])
    [skipped] = plan.skip(MON + dt.timedelta(days=3))
    assert isinstance(skipped, PlanSkipped)
    with pytest.raises(DomainError):
        plan.skip(MON + dt.timedelta(days=3))  # al overgeslagen


def test_only_a_repeat_can_be_skipped():
    with pytest.raises(DomainError):
        Plan([DENTIST]).skip(MON)


# ---- Verzetten en bewerken ----

def test_reschedule_plan_it_and_back_to_someday():
    plan = Plan([SOMEDAY])
    [planned] = plan.reschedule(MON, dt.time(10))
    assert (planned.day, planned.time) == (MON, dt.time(10))
    [back] = plan.reschedule(None, None)
    assert back.day is None


def test_rescheduling_to_the_same_date_and_time_gives_no_event():
    assert Plan([DENTIST]).reschedule(MON, dt.time(14)) == []


def test_rescheduling_a_time_without_a_date_is_refused():
    with pytest.raises(DomainError):
        Plan([DENTIST]).reschedule(None, dt.time(9))


def test_a_repeat_cannot_go_to_someday():
    with pytest.raises(DomainError):
        Plan([GYM]).reschedule(None, None)


def test_edit_title_note_duration_and_repeat():
    [edited] = Plan([DENTIST]).edit("Dentist (check-up)", "Floss first", 45, Repeat(Frequency.MONTHLY))
    assert isinstance(edited, PlanEdited) and edited.repeat.every is Frequency.MONTHLY
    assert Plan([DENTIST]).edit("Dentist", "", None, None) == []  # niets veranderd
    with pytest.raises(DomainError):
        Plan([DENTIST]).edit(" ", "", None, None)
    with pytest.raises(DomainError):
        Plan([SOMEDAY]).edit("Paint", "", None, Repeat(Frequency.DAILY))  # herhaling zonder datum


@pytest.mark.parametrize("action", [
    lambda p: p.mark_done(MON), lambda p: p.reschedule(MON, None), lambda p: p.skip(MON),
    lambda p: p.edit("x", "", None, None), lambda p: p.remove(), lambda p: p.reopen(MON),
])
def test_a_removed_plan_cannot_change(action):
    with pytest.raises(DomainError):
        action(Plan([GYM, PlanRemoved("3", "Gym")]))


def test_unknown_plan_is_refused():
    with pytest.raises(DomainError):
        Plan([]).mark_done(MON)


# ---- Herinneringen ----

def test_same_reminder_settings_give_no_event():
    settings = ReminderSettings([])
    [changed] = settings.change(True, 60, False, 180, dt.time(8))
    assert isinstance(changed, ReminderSettingsChanged)
    assert ReminderSettings([changed]).change(True, 60, False, 180, dt.time(8)) == []
    assert ReminderSettings([]).change(*ReminderSettings.DEFAULT) == []  # de standaard is al zo


def test_reminder_lead_must_be_positive():
    with pytest.raises(DomainError):
        ReminderSettings([]).change(True, 0, True, 180, dt.time(9))


# ---- Commands via de event store ----

def make_handler():
    store = EventStore(":memory:", EVENT_TYPES)
    return PlannerCommandHandler(store), store


def test_handler_stores_each_plan_in_its_own_stream():
    handler, store = make_handler()
    [added] = handler.handle(AddPlan("Dentist", MON, dt.time(14)))
    handler.handle(MarkPlanDone(added.plan_id, MON))
    handler.handle(ReopenPlan(added.plan_id, MON))
    handler.handle(ReschedulePlan(added.plan_id, MON + dt.timedelta(days=1), dt.time(9)))
    handler.handle(EditPlan(added.plan_id, "Dentist", "Card", 30, None))
    handler.handle(RemovePlan(added.plan_id))
    types = [type(e) for e in store.load_stream(added.plan_id)]
    assert types == [PlanAdded, PlanDone, PlanReopened, PlanRescheduled, PlanEdited, PlanRemoved]


def test_handler_skips_and_unchanged_reschedule_stores_nothing():
    handler, store = make_handler()
    [added] = handler.handle(AddPlan("Gym", MON, dt.time(7), repeat=Repeat(Frequency.DAILY)))
    handler.handle(SkipPlan(added.plan_id, MON))
    assert handler.handle(ReschedulePlan(added.plan_id, MON, dt.time(7))) == []
    assert store.count() == 2


def test_reminder_settings_live_in_their_own_stream():
    handler, store = make_handler()
    handler.handle(ChangeReminderSettings(True, 60, False, 180, dt.time(8)))
    [event] = store.load_stream("planner-settings")
    assert (event.first_minutes, event.second_enabled, event.all_day_time) == (60, False, dt.time(8))
