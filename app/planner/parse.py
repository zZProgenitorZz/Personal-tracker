"""Snel invoeren in gewone taal, Nederlands en Engels door elkaar: "Tandarts vr 14u",
"elke ma en do 7:00 sporten", "12 okt verjaardag Sam". Een kleine eigen parser, geen library.

De parser haalt datum, tijd en herhaling uit de tekst; wat overblijft is de titel. Niets
herkend = een plan voor Someday. Alles is "de eerstvolgende": een weekdag of datum zonder
jaar die al voorbij is, schuift door. Vandaag telt mee zolang de tijd nog niet voorbij is.

Korte weekdagen die ook gewone woorden zijn ("do the dishes", "zo snel mogelijk", "bellen met
ma") tellen alleen naast een tijd, of na "op", "on", "volgende", "next", "elke" of "every".
Een tijdvak ("14:00-15:30", "12u-13u") wordt een begintijd met een duur.
"""
import datetime as dt
import re
from dataclasses import dataclass

from .events import Frequency, Repeat

WEEKDAYS = {
    "maandag": 0, "ma": 0, "monday": 0, "mon": 0,
    "dinsdag": 1, "di": 1, "tuesday": 1, "tues": 1, "tue": 1,
    "woensdag": 2, "wo": 2, "wednesday": 2, "wed": 2,
    "donderdag": 3, "do": 3, "thursday": 3, "thurs": 3, "thur": 3, "thu": 3,
    "vrijdag": 4, "vr": 4, "friday": 4, "fri": 4,
    "zaterdag": 5, "za": 5, "saturday": 5, "sat": 5,
    "zondag": 6, "zo": 6, "sunday": 6, "sun": 6,
}
AMBIGUOUS = {"ma", "di", "wo", "do", "zo", "mon", "wed", "sat", "sun"}
MONTHS = {
    "januari": 1, "january": 1, "jan": 1, "februari": 2, "february": 2, "feb": 2,
    "maart": 3, "march": 3, "mrt": 3, "mar": 3, "april": 4, "apr": 4, "mei": 5, "may": 5,
    "juni": 6, "june": 6, "jun": 6, "juli": 7, "july": 7, "jul": 7, "augustus": 8, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}
SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _words(names) -> str:
    return "|".join(sorted(names, key=len, reverse=True))


WD = _words(WEEKDAYS)
MONTH = _words(MONTHS)
EVERY = r"(?:elke|iedere|every|each)"
NEXT_WEEK = r"(?:volgende\s+week|next\s+week)"
B, E = r"(?<![\w])", r"(?![\w])"  # woordgrenzen (ook rond ":" en ".")
CONNECTORS = {"om", "at", "op", "on", "en", "and", "@", "-", "–", "&"}
COMING = r"(?:volgende|komende|aanstaande|deze|next|this|coming)"  # "volgende maandag" = de eerstvolgende
# Een tijd met een herkenbaar teken: 14:00, 14.30, 14u, 20u30, 14 uur.
CLOCK = r"(\d{1,2})(?:[:.](\d{2})|u(\d{2})?(?!\w)|\s*uur(?!\w))"


@dataclass(frozen=True)
class Parsed:
    title: str
    day: dt.date | None = None
    time: dt.time | None = None
    repeat: Repeat | None = None
    duration_min: int | None = None  # uit een tijdvak, zoals "14:00-15:30"


class _Text:
    """De invoer, met bij te houden welke stukken al herkend zijn."""

    def __init__(self, text: str):
        self.original = text
        self.low = text.lower()
        self.used = [False] * len(text)

    def find(self, pattern: str):
        for match in re.finditer(pattern, self.low):
            if not any(self.used[match.start():match.end()]):
                yield match

    def take(self, match) -> None:
        for i in range(match.start(), match.end()):
            self.used[i] = True

    def gap_is_blank(self, start: int, end: int) -> bool:
        return self.low[min(start, end):max(start, end)].strip() == ""

    def rest(self) -> str:
        kept = "".join(" " if used else ch for ch, used in zip(self.original, self.used))
        words = kept.split()
        while words and words[0].lower().strip(",;:") in CONNECTORS | {""}:
            words.pop(0)
        while words and words[-1].lower().strip(",;:") in CONNECTORS | {""}:
            words.pop()
        return " ".join(words).strip(" ,;:-–")


def parse(text: str, now: dt.datetime) -> Parsed:
    """`now` is lokale tijd zonder tijdzone (de klok van deze pc)."""
    t = _Text(text)
    today = now.date()
    repeat = _repeat(t)
    day = _next_week(t, today) or _relative(t, today) or _date(t, today)
    time, time_span, duration = _time_range(t)
    if time is None:
        time, time_span = _time(t)
    weekday = None if day else _weekday(t, time_span, has_repeat=repeat is not None)

    if weekday is not None:
        day = _upcoming(weekday, today, now, time)
    if repeat is not None and day is None:
        if repeat.every is Frequency.WEEKLY and repeat.weekdays:
            day = min(_upcoming(w, today, now, time) for w in repeat.weekdays)
        else:
            day = today
    if repeat is not None and repeat.every is Frequency.WEEKLY and not repeat.weekdays:
        repeat = Repeat(Frequency.WEEKLY, (day.weekday(),))
    if time is not None and day is None:
        # Alleen een tijd: vandaag als die nog komt, anders morgen.
        day = today if time > now.time() else today + dt.timedelta(days=1)
    return Parsed(t.rest(), day, time, repeat, duration)


# ---- De onderdelen ----

def _repeat(t: _Text) -> Repeat | None:
    for pattern, every in [(rf"{B}{EVERY}\s+(?:dag|day){E}", Frequency.DAILY),
                           (rf"{B}{EVERY}\s+(?:maand|month){E}", Frequency.MONTHLY),
                           (rf"{B}{EVERY}\s+week{E}", Frequency.WEEKLY)]:
        for match in t.find(pattern):
            t.take(match)
            return Repeat(every)
    list_of_days = rf"{B}{EVERY}\s+(?:{WD}){E}(?:\s*(?:,|en|and|&|\+)\s*(?:{WD}){E})*"
    for match in t.find(list_of_days):
        t.take(match)
        days = re.findall(rf"{B}({WD}){E}", match.group(0))
        return Repeat(Frequency.WEEKLY, tuple(sorted({WEEKDAYS[d] for d in days})))
    return None


def _next_week(t: _Text, today: dt.date) -> dt.date | None:
    monday = today + dt.timedelta(days=7 - today.weekday())
    for pattern in [rf"{B}{NEXT_WEEK}\s+(?:op\s+|on\s+)?({WD}){E}", rf"{B}({WD})\s+{NEXT_WEEK}{E}"]:
        for match in t.find(pattern):
            t.take(match)
            return monday + dt.timedelta(days=WEEKDAYS[match.group(1)])
    return None


def _relative(t: _Text, today: dt.date) -> dt.date | None:
    for pattern, days in [(rf"{B}(?:overmorgen|day after tomorrow){E}", 2),
                          (rf"{B}(?:morgenochtend|morgenmiddag|morgenavond|tomorrow\s+(?:morning|afternoon|night|evening)){E}", 1),
                          (rf"{B}(?:vandaag|today|vanochtend|vanmiddag|vanavond|tonight|this\s+(?:morning|afternoon|evening)){E}", 0),
                          (rf"{B}(?:morgen|tomorrow){E}", 1)]:
        for match in t.find(pattern):
            t.take(match)
            return today + dt.timedelta(days=days)
    return None


def _date(t: _Text, today: dt.date) -> dt.date | None:
    patterns = [
        (rf"{B}(?:op\s+|on\s+)?(\d{{1,2}})\s*({MONTH})\.?{E}(?:\s+(\d{{4}}){E})?", lambda m: (m[1], MONTHS[m[2]], m[3])),
        (rf"{B}(?:op\s+|on\s+)?({MONTH})\.?\s+(\d{{1,2}}){E}(?:,?\s+(\d{{4}}){E})?", lambda m: (m[2], MONTHS[m[1]], m[3])),
        # Niet vast aan een tijd: in "10:30-12:15" is "30-12" geen datum.
        (rf"(?<![\w:.])(?:op\s+|on\s+)?(\d{{1,2}})[/-](\d{{1,2}})(?:[/-](\d{{4}}|\d{{2}}))?{E}(?![:.]\d)",
         lambda m: (m[1], m[2], m[3])),
    ]
    for pattern, parts in patterns:
        for match in t.find(pattern):
            day, month, year = parts(match)
            found = _valid_date(int(day), int(month), year, today)
            if found:
                t.take(match)
                return found
    return None


def _valid_date(day: int, month: int, year: str | None, today: dt.date) -> dt.date | None:
    try:
        if year:
            return dt.date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        found = dt.date(today.year, month, day)
        return found if found >= today else dt.date(today.year + 1, month, day)
    except ValueError:
        return None


def _time_range(t: _Text):
    """ "14:00-15:30", "van 9.00 tot 10.15", "12u-13u": begintijd en duur."""
    end = r"(\d{1,2})(?:[:.](\d{2})|u(\d{2})?(?!\w)|\s*uur(?!\w))?"
    pattern = rf"{B}(?:om\s+|at\s+|van\s+|from\s+)?{CLOCK}\s*(?:-|–|tot|to|until)\s*{end}{E}"
    for match in t.find(pattern):
        h1, m1a, m1b, h2, m2a, m2b = match.groups()
        start = _valid_time(int(h1), int(m1a or m1b or 0), None)
        stop = _valid_time(int(h2), int(m2a or m2b or 0), None)
        if start and stop:
            minutes = (stop.hour * 60 + stop.minute) - (start.hour * 60 + start.minute)
            if minutes > 0:
                t.take(match)
                return start, (match.start(), match.end()), minutes
    return None, None, None


def _time(t: _Text) -> tuple[dt.time | None, tuple[int, int] | None]:
    patterns = [
        rf"{B}(?:om\s+|at\s+|@\s*)?(\d{{1,2}})[:.](\d{{2}})\s*(am|pm)?{E}",
        rf"{B}(?:om\s+|at\s+|@\s*)?(\d{{1,2}})(?:u(\d{{2}})?|\s*uur){E}",
        rf"{B}(?:om\s+|at\s+|@\s*)?(\d{{1,2}})\s*(am|pm){E}",
        rf"{B}(?:om|at|@)\s*(\d{{1,2}}){E}(?![:./-]\d)",
    ]
    for pattern in patterns:
        for match in t.find(pattern):
            groups = list(match.groups()) + [None] * 3
            hour, minute, half = groups[0], None, None
            for g in groups[1:]:
                if g in ("am", "pm"):
                    half = g
                elif g is not None and minute is None:
                    minute = g
            found = _valid_time(int(hour), int(minute or 0), half)
            if found:
                t.take(match)
                return found, (match.start(), match.end())
    return None, None


def _valid_time(hour: int, minute: int, half: str | None) -> dt.time | None:
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return dt.time(hour, minute)
    return None


def _weekday(t: _Text, time_span, has_repeat: bool) -> int | None:
    if has_repeat:
        return None
    for match in t.find(rf"{B}(?:(op|on)\s+)?(?:({COMING})\s+)?({WD}){E}"):
        name = match.group(3)
        if name in AMBIGUOUS and not (match.group(1) or match.group(2)):
            near_time = time_span and (t.gap_is_blank(match.end(), time_span[0])
                                       or t.gap_is_blank(time_span[1], match.start()))
            if not near_time:
                continue
        t.take(match)
        return WEEKDAYS[name]
    return None


def _upcoming(weekday: int, today: dt.date, now: dt.datetime, time: dt.time | None) -> dt.date:
    """De eerstvolgende `weekday`; vandaag telt mee als de tijd nog niet voorbij is."""
    ahead = (weekday - today.weekday()) % 7
    if ahead == 0 and time is not None and time <= now.time():
        ahead = 7
    return today + dt.timedelta(days=ahead)


# ---- Terug naar tekst, voor de preview onder het invoerveld ----

def nice_day(day: dt.date, today: dt.date) -> str:
    text = f"{SHORT_DAYS[day.weekday()]} {day.day} {day:%b}"
    return text if day.year == today.year else f"{text} {day.year}"


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 12 -> '12th', 22 -> '22nd'."""
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def describe_repeat(repeat: Repeat, start: dt.date | None = None) -> str:
    """'every Mon, Thu', 'every day', 'every month on the 12th' (met `start`), eventueel 'until ...'."""
    if repeat.every is Frequency.DAILY:
        text = "every day"
    elif repeat.every is Frequency.MONTHLY:
        text = f"every month on the {ordinal(start.day)}" if start else "every month"
    else:
        text = "every " + ", ".join(SHORT_DAYS[d] for d in repeat.weekdays)
    return f"{text} until {repeat.until.day} {repeat.until:%b %Y}" if repeat.until else text


def describe(parsed: Parsed, today: dt.date) -> str:
    parts = [parsed.title or "Add a title"]
    if parsed.day is None:
        parts.append("Someday")
    else:
        parts.append(("from " if parsed.repeat else "") + nice_day(parsed.day, today))
    if parsed.time and parsed.duration_min:
        end = dt.datetime.combine(dt.date.today(), parsed.time) + dt.timedelta(minutes=parsed.duration_min)
        parts.append(f"{parsed.time:%H:%M}–{end:%H:%M}")
    elif parsed.time:
        parts.append(f"{parsed.time:%H:%M}")
    if parsed.repeat:
        parts.append(describe_repeat(parsed.repeat, parsed.day))
    return " · ".join(parts)
