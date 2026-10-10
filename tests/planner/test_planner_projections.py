"""Agenda, herinneringen en activiteit: given events, then wat de projecties tonen. Met een vaste klok."""
import datetime as dt

from app.eventstore import EventStore
from app.planner.events import (
    EVENT_TYPES, Frequency, PlanAdded, PlanDone, PlanEdited, PlanRemoved, PlanReopened, PlanRescheduled, PlanSkipped,
    ReminderSettingsChanged, Repeat,
)
from app.planner.projections import AgendaProjection, PlannerActivityProjection

MON = dt.date(2026, 10, 12)
THU = MON + dt.timedelta(days=3)


def given(*events) -> AgendaProjection:
    agenda = AgendaProjection()
    for event in events:
        agenda.apply(event)
    return agenda


def plan(pid, title, day=MON, time=None, repeat=None, duration=None):
    return PlanAdded(pid, title, day, time, duration, "", repeat)


def titles(occurrences):
    return [o.title for o in occurrences]


# ---- Dagen en weken ----

def test_a_day_lists_all_day_first_then_by_time():
    agenda = given(plan("1", "Dentist", time=dt.time(14)), plan("2", "Birthday Sam"),
                   plan("3", "Standup", time=dt.time(9, 30)), plan("4", "Other day", day=THU))
    assert titles(agenda.day(MON)) == ["Birthday Sam", "Standup", "Dentist"]


def test_repeats_are_worked_out_and_skips_left_out():
    gym = Repeat(Frequency.WEEKLY, (0, 3))
    agenda = given(plan("1", "Gym", time=dt.time(7), repeat=gym), PlanSkipped("1", THU))
    assert titles(agenda.day(MON + dt.timedelta(days=7))) == ["Gym"]
    assert agenda.day(THU) == [] and agenda.day(MON + dt.timedelta(days=1)) == []
    [occurrence] = agenda.day(MON)
    assert occurrence.on == MON and occurrence.is_repeat


def test_done_is_per_occurrence():
    agenda = given(plan("1", "Gym", repeat=Repeat(Frequency.DAILY)), PlanDone("1", MON))
    assert agenda.day(MON)[0].done and not agenda.day(THU)[0].done


def test_week_has_seven_days_from_monday():
    agenda = given(plan("1", "Dentist", day=THU, time=dt.time(14)))
    week = agenda.week(MON)
    assert [d for d, _ in week] == [MON + dt.timedelta(days=i) for i in range(7)]
    assert titles(week[3][1]) == ["Dentist"]


def test_reschedule_edit_and_remove_change_the_agenda():
    agenda = given(plan("1", "Dentist", time=dt.time(14)),
                   PlanRescheduled("1", THU, dt.time(10)),
                   PlanEdited("1", "Dentist check-up", "Card", 30, None),
                   plan("2", "Gone"), PlanRemoved("2", "Gone"))
    assert agenda.day(MON) == []
    [o] = agenda.day(THU)
    assert (o.title, o.time, o.plan.note, o.plan.duration_min) == ("Dentist check-up", dt.time(10), "Card", 30)


def test_someday_lists_plans_without_a_date_that_are_not_done():
    agenda = given(plan("1", "Paint the hall", day=None), plan("2", "Learn Korean", day=None),
                   plan("3", "Dentist"), plan("4", "Fix bike", day=None), PlanDone("4", MON))
    assert [p.title for p in agenda.someday()] == ["Paint the hall", "Learn Korean"]


# ---- Herinneringen ----

def due(agenda, *at, minutes=1):
    return agenda.due(dt.datetime(*at), dt.timedelta(minutes=minutes))


def test_timed_plan_reminds_a_day_and_three_hours_before():
    agenda = given(plan("1", "Dentist", time=dt.time(14)))
    [first] = due(agenda, 2026, 10, 11, 14, 0)
    assert first.text == "Tomorrow: Dentist at 14:00" and first.key == "1/2026-10-12/1"
    [second] = due(agenda, 2026, 10, 12, 11, 0, 30)
    assert second.text == "In 3 hours: Dentist at 14:00"
    assert due(agenda, 2026, 10, 12, 11, 2) == []  # buiten het venster van een minuut


def test_all_day_plan_reminds_the_day_before_and_that_day_at_nine():
    agenda = given(plan("1", "Birthday Sam"))
    assert [r.text for r in due(agenda, 2026, 10, 11, 9, 0)] == ["Tomorrow: Birthday Sam"]
    assert [r.text for r in due(agenda, 2026, 10, 12, 9, 0)] == ["Today: Birthday Sam"]


def test_reminder_settings_are_used():
    agenda = given(plan("1", "Dentist", time=dt.time(14)),
                   ReminderSettingsChanged(False, 1440, True, 15, dt.time(8)))
    assert due(agenda, 2026, 10, 11, 14, 0) == []  # eerste herinnering staat uit
    assert [r.text for r in due(agenda, 2026, 10, 12, 13, 45)] == ["In 15 minutes: Dentist at 14:00"]


def test_done_skipped_and_someday_plans_do_not_remind():
    agenda = given(plan("1", "Dentist", time=dt.time(14)), PlanDone("1", MON),
                   plan("2", "Gym", time=dt.time(14), repeat=Repeat(Frequency.DAILY)), PlanSkipped("2", MON),
                   plan("3", "Someday", day=None))
    assert due(agenda, 2026, 10, 11, 14, 0) == []


def test_each_occurrence_of_a_repeat_reminds():
    agenda = given(plan("1", "Gym", time=dt.time(7), repeat=Repeat(Frequency.WEEKLY, (0, 3))))
    assert [r.key for r in due(agenda, 2026, 10, 15, 4, 0)] == ["1/2026-10-15/2"]


# ---- Activiteit en herbouwen ----

def test_activity_counts_reschedules_per_plan():
    activity = PlannerActivityProjection()
    for event in [plan("1", "Dentist"), PlanRescheduled("1", THU, None), PlanRescheduled("1", MON, None),
                  plan("2", "Gym")]:
        activity.apply(event)
    assert activity.rescheduled("1") == 2 and activity.rescheduled("2") == 0


def test_rebuilt_agenda_equals_the_live_one():
    store = EventStore(":memory:", EVENT_TYPES)
    live = AgendaProjection()
    store.subscribe(live.apply)
    store.append("1", [plan("1", "Gym", time=dt.time(7), repeat=Repeat(Frequency.WEEKLY, (0, 3))),
                       PlanSkipped("1", THU), PlanDone("1", MON)])
    store.append("2", [plan("2", "Dentist", day=None), PlanRescheduled("2", THU, dt.time(14))])
    store.append("planner-settings", [ReminderSettingsChanged(True, 60, True, 180, dt.time(8))])
    rebuilt = AgendaProjection()
    for event in store.load_all():
        rebuilt.apply(event)
    assert rebuilt.week(MON) == live.week(MON) and rebuilt.reminder_settings == live.reminder_settings
    assert rebuilt.due(dt.datetime(2026, 10, 15, 13, 0), dt.timedelta(minutes=1)) == \
        live.due(dt.datetime(2026, 10, 15, 13, 0), dt.timedelta(minutes=1))


def test_someday_done_lists_the_latest_first_and_at_most_ten():
    events = [plan(str(i), f"Plan {i}", day=None) for i in range(12)]
    events += [PlanDone(str(i), MON + dt.timedelta(days=i)) for i in range(12)]
    events += [plan("x", "Dentist"), PlanDone("x", MON)]                     # met datum: niet in Someday
    agenda = given(*events)
    done = agenda.someday_done()
    assert [p.title for p, _ in done] == [f"Plan {i}" for i in range(11, 1, -1)]
    assert done[0][1] == MON + dt.timedelta(days=11)                         # wanneer het af was


def test_reopened_someday_plan_goes_back_to_someday():
    agenda = given(plan("1", "Paint the hall", day=None), PlanDone("1", MON), PlanReopened("1", MON))
    assert agenda.someday_done() == [] and [p.title for p in agenda.someday()] == ["Paint the hall"]
