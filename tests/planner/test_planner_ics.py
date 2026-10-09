"""Eén plan als iCalendar-bestand (RFC 5545), om naar je iPhone te sturen. Vaste voorbeelden."""
import datetime as dt

from app.planner.events import Frequency, Repeat
from app.planner.ics import plan_to_ics
from app.planner.projections import PlanEntry

STAMP = dt.datetime(2026, 10, 9, 8, 30, tzinfo=dt.timezone.utc)
ADDED = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
DEFAULT = (True, 1440, True, 180, dt.time(9))


def entry(**fields):
    values = dict(plan_id="abc-123", title="Dentist", day=dt.date(2026, 10, 12), time=dt.time(14),
                  duration_min=None, note="", repeat=None, added_at=ADDED)
    return PlanEntry(**{**values, **fields})


def lines(text):
    assert "\n" not in text.replace("\r\n", "")  # alleen CRLF
    return text.split("\r\n")


def test_timed_plan_with_default_reminders():
    assert lines(plan_to_ics(entry(duration_min=30), DEFAULT, STAMP)) == [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Progen//Planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        "UID:abc-123@progen",
        "DTSTAMP:20261009T083000Z",
        "SUMMARY:Dentist",
        "DTSTART:20261012T140000",
        "DTEND:20261012T143000",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:Dentist",
        "TRIGGER:-P1D",
        "END:VALARM",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:Dentist",
        "TRIGGER:-PT3H",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]


def test_without_duration_an_hour_is_assumed():
    assert "DTEND:20261012T150000" in lines(plan_to_ics(entry(), DEFAULT, STAMP))


def test_all_day_plan_uses_dates_and_reminds_the_day_before_and_that_day_at_nine():
    out = lines(plan_to_ics(entry(time=None), DEFAULT, STAMP))
    assert "DTSTART;VALUE=DATE:20261012" in out and "DTEND;VALUE=DATE:20261013" in out
    assert [line for line in out if line.startswith("TRIGGER")] == ["TRIGGER:-PT15H", "TRIGGER:PT9H"]


def test_reminders_follow_the_settings_at_download_time():
    out = lines(plan_to_ics(entry(), (False, 1440, True, 15, dt.time(8)), STAMP))
    assert [line for line in out if line.startswith("TRIGGER")] == ["TRIGGER:-PT15M"]
    assert "BEGIN:VALARM" not in plan_to_ics(entry(), (False, 1440, False, 180, dt.time(9)), STAMP)


def test_repeats_become_rrules_with_skipped_days_as_exdates():
    weekly = entry(repeat=Repeat(Frequency.WEEKLY, (0, 3), dt.date(2026, 12, 31)),
                   skipped={dt.date(2026, 10, 15)})
    out = lines(plan_to_ics(weekly, DEFAULT, STAMP))
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO,TH;UNTIL=20261231T235959" in out
    assert "EXDATE:20261015T140000" in out
    assert "RRULE:FREQ=DAILY" in lines(plan_to_ics(entry(repeat=Repeat(Frequency.DAILY)), DEFAULT, STAMP))
    all_day = entry(time=None, repeat=Repeat(Frequency.DAILY, until=dt.date(2026, 11, 1)))
    assert "RRULE:FREQ=DAILY;UNTIL=20261101" in lines(plan_to_ics(all_day, DEFAULT, STAMP))


def test_monthly_on_the_31st_falls_on_the_last_day_on_the_iphone_too():
    monthly = entry(day=dt.date(2026, 1, 31), repeat=Repeat(Frequency.MONTHLY))
    assert "RRULE:FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1" in lines(plan_to_ics(monthly, DEFAULT, STAMP))
    assert "RRULE:FREQ=MONTHLY;BYMONTHDAY=12" in lines(
        plan_to_ics(entry(repeat=Repeat(Frequency.MONTHLY)), DEFAULT, STAMP))


def test_text_is_escaped():
    out = plan_to_ics(entry(title="Lunch; Anna, Bob", note="Bring\\nothing\nTable 4"), DEFAULT, STAMP)
    assert "SUMMARY:Lunch\\; Anna\\, Bob" in lines(out)
    assert "DESCRIPTION:Bring\\\\nothing\\nTable 4" in lines(out)


def test_long_lines_are_folded_at_75_bytes_without_breaking_characters():
    note = ("Ééntje " * 30).strip()
    out = plan_to_ics(entry(note=note), DEFAULT, STAMP)
    for line in lines(out):
        assert len(line.encode("utf-8")) <= 75
    unfolded = out.replace("\r\n ", "")
    assert f"DESCRIPTION:{note}" in unfolded.split("\r\n")
