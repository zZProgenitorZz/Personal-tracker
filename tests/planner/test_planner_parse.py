"""Snel invoeren in gewone taal: Nederlands en Engels door elkaar. Met een vaste klok."""
import datetime as dt

import pytest

from app.planner.events import Frequency
from app.planner.parse import describe, parse

NOW = dt.datetime(2026, 10, 8, 10, 0)   # donderdag 8 oktober 2026, 10:00
TODAY = NOW.date()
D = lambda m, d, y=2026: dt.date(y, m, d)  # noqa: E731
T = dt.time


def p(text):
    return parse(text, NOW)


@pytest.mark.parametrize("text, title, day, time", [
    # Dagen
    ("vandaag boodschappen", "boodschappen", TODAY, None),
    ("Groceries today", "Groceries", TODAY, None),
    ("Bel Mama morgen", "Bel Mama", D(10, 9), None),
    ("dentist tomorrow 3pm", "dentist", D(10, 9), T(15)),
    ("overmorgen om 9 bellen", "bellen", D(10, 10), T(9)),
    ("Tandarts vr 14u", "Tandarts", D(10, 9), T(14)),
    ("zondag brunch", "brunch", D(10, 11), None),
    ("Brunch sunday 11:30", "Brunch", D(10, 11), T(11, 30)),
    ("Standup do 14:00", "Standup", TODAY, T(14)),            # vandaag telt mee: 14:00 komt nog
    ("Standup do 9:00", "Standup", D(10, 15), T(9)),           # 9:00 is voorbij: volgende week
    ("Yoga donderdag", "Yoga", TODAY, None),                  # zonder tijd telt vandaag mee
    ("volgende week di yoga", "yoga", D(10, 13), None),
    ("Yoga next week tue 18:00", "Yoga", D(10, 13), T(18)),
    ("Kapper wo volgende week", "Kapper", D(10, 14), None),
    # Datums (dag eerst; zonder jaar de eerstvolgende)
    ("12 okt verjaardag Sam", "verjaardag Sam", D(10, 12), None),
    ("Verjaardag Sam 12 oktober", "Verjaardag Sam", D(10, 12), None),
    ("Party 12/10 20u30", "Party", D(10, 12), T(20, 30)),
    ("Party 12-10", "Party", D(10, 12), None),
    ("Party oct 12", "Party", D(10, 12), None),
    ("Belasting 1 okt", "Belasting", D(10, 1, 2027), None),   # al voorbij: volgend jaar
    ("Ski 5 jan 2027", "Ski", D(1, 5, 2027), None),
    ("Concert 3/4", "Concert", D(4, 3, 2027), None),
    # Tijden
    ("Meeting 14.30", "Meeting", TODAY, T(14, 30)),
    ("Meeting at 9am", "Meeting", D(10, 9), T(9)),            # alleen een tijd die voorbij is: morgen
    ("Film 20u", "Film", TODAY, T(20)),
    ("Lunch @ 12:15", "Lunch", TODAY, T(12, 15)),
    ("Late call 12am", "Late call", D(10, 9), T(0)),
    # Niets herkend: Someday
    ("Learn Korean", "Learn Korean", None, None),
    ("do the dishes", "do the dishes", None, None),           # "do" is hier geen donderdag
    ("zo snel mogelijk bellen", "zo snel mogelijk bellen", None, None),
    ("bellen met ma", "bellen met ma", None, None),
    ("Room 101 painten", "Room 101 painten", None, None),
])
def test_dates_times_and_titles(text, title, day, time):
    parsed = p(text)
    assert (parsed.title, parsed.day, parsed.time, parsed.repeat) == (title, day, time, None)


@pytest.mark.parametrize("text, title, every, weekdays, day, time", [
    ("elke maandag gym", "gym", Frequency.WEEKLY, (0,), D(10, 12), None),
    ("Sporten elke ma en do 7:00", "Sporten", Frequency.WEEKLY, (0, 3), D(10, 12), T(7)),
    ("Sporten elke ma en do 19:00", "Sporten", Frequency.WEEKLY, (0, 3), TODAY, T(19)),
    ("every monday standup 9:30", "standup", Frequency.WEEKLY, (0,), D(10, 12), T(9, 30)),
    ("Every tue, thu tennis", "tennis", Frequency.WEEKLY, (1, 3), TODAY, None),  # vandaag is do
    ("Vitamines elke dag", "Vitamines", Frequency.DAILY, (), TODAY, None),
    ("Rent every month 1 nov", "Rent", Frequency.MONTHLY, (), D(11, 1), None),
    ("Huur elke maand", "Huur", Frequency.MONTHLY, (), TODAY, None),
])
def test_repeats(text, title, every, weekdays, day, time):
    parsed = p(text)
    assert (parsed.title, parsed.repeat.every, parsed.repeat.weekdays, parsed.day, parsed.time) == \
        (title, every, weekdays, day, time)


def test_case_and_extra_spaces_in_the_title_are_kept_tidy():
    assert p("  Tandarts   VR   14:00 ").title == "Tandarts"
    assert p("Call Anna, tomorrow").title == "Call Anna"


def test_describe_shows_what_the_app_makes_of_it():
    assert describe(p("Tandarts vr 14:00"), TODAY) == "Tandarts · Fri 9 Oct · 14:00"
    assert describe(p("Learn Korean"), TODAY) == "Learn Korean · Someday"
    assert describe(p("Gym elke ma en do 7:00"), TODAY) == "Gym · from Mon 12 Oct · 07:00 · every Mon, Thu"
    assert describe(p("Ski 5 jan 2027"), TODAY) == "Ski · Tue 5 Jan 2027"
    assert describe(p("Vitamines elke dag"), TODAY) == "Vitamines · from Thu 8 Oct · every day"
    assert describe(p("morgen 14u"), TODAY) == "Add a title · Fri 9 Oct · 14:00"
