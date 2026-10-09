"""Eén plan als iCalendar-bestand (RFC 5545), om naar je iPhone te sturen. Zelf geschreven.

- Tijden zijn lokale tijd zonder tijdzone ("floating"): 14:00 blijft 14:00 op je iPhone.
- Hele dag: DTSTART/DTEND als VALUE=DATE.
- Herhaling als RRULE; overgeslagen keren als EXDATE.
- Herinneringen als VALARM, volgens de instelling in Settings op het moment van downloaden.
- CRLF-regeleinden, tekst ge-escaped, regels gevouwen op 75 bytes.
"""
import datetime as dt

from .events import Frequency
from .projections import PlanEntry

DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
DEFAULT_MINUTES = 60  # zonder duur: een uur


def plan_to_ics(plan: PlanEntry, reminders: tuple, stamp: dt.datetime) -> str:
    """`reminders` is de instelling uit Settings: (eerste aan, minuten, tweede aan, minuten, tijd hele dag)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Progen//Planner//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "BEGIN:VEVENT",
             f"UID:{plan.plan_id}@progen",
             f"DTSTAMP:{stamp.astimezone(dt.timezone.utc):%Y%m%dT%H%M%SZ}",
             f"SUMMARY:{escape(plan.title)}"]
    if plan.note:
        lines.append(f"DESCRIPTION:{escape(plan.note)}")
    lines += _when(plan)
    first_on, first_min, second_on, second_min, all_day_time = reminders
    for enabled, minutes in [(first_on, first_min), (second_on, second_min)]:
        if enabled:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape(plan.title)}",
                      f"TRIGGER:{_trigger(plan, minutes, all_day_time)}", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "".join(fold(line) + "\r\n" for line in lines)


def _when(plan: PlanEntry) -> list[str]:
    if plan.day is None:  # Someday: als hele dag op de dag van downloaden zou verwarren; gewoon vandaag
        day = dt.date.today()
        return [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}"]
    if plan.time is None:
        lines = [f"DTSTART;VALUE=DATE:{plan.day:%Y%m%d}",
                 f"DTEND;VALUE=DATE:{plan.day + dt.timedelta(days=1):%Y%m%d}"]
    else:
        start = dt.datetime.combine(plan.day, plan.time)
        end = start + dt.timedelta(minutes=plan.duration_min or DEFAULT_MINUTES)
        lines = [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}"]
    if plan.repeat is not None:
        lines.append(f"RRULE:{_rrule(plan)}")
        for skipped in sorted(plan.skipped):
            lines.append(f"EXDATE;VALUE=DATE:{skipped:%Y%m%d}" if plan.time is None
                         else f"EXDATE:{dt.datetime.combine(skipped, plan.time):%Y%m%dT%H%M%S}")
    return lines


def _rrule(plan: PlanEntry) -> str:
    repeat = plan.repeat
    if repeat.every is Frequency.DAILY:
        parts = ["FREQ=DAILY"]
    elif repeat.every is Frequency.WEEKLY:
        parts = ["FREQ=WEEKLY", "BYDAY=" + ",".join(DAY_CODES[d] for d in repeat.weekdays)]
    elif plan.day.day > 28:
        # Op de 29e-31e: de laatste dag als de maand korter is, net als in Progen.
        days = ",".join(str(d) for d in range(28, plan.day.day + 1))
        parts = ["FREQ=MONTHLY", f"BYMONTHDAY={days}", "BYSETPOS=-1"]
    else:
        parts = ["FREQ=MONTHLY", f"BYMONTHDAY={plan.day.day}"]
    if repeat.until is not None:
        # UNTIL in hetzelfde soort waarde als DTSTART: een datum, of het eind van die dag.
        parts.append(f"UNTIL={repeat.until:%Y%m%d}" if plan.time is None else f"UNTIL={repeat.until:%Y%m%d}T235959")
    return ";".join(parts)


def _trigger(plan: PlanEntry, minutes: int, all_day_time: dt.time) -> str:
    if plan.time is not None:
        return duration(-minutes)
    # Hele dag: de herinnering valt om all_day_time, een of meer dagen van tevoren (of die dag zelf).
    days = minutes // (24 * 60)
    return duration(-days * 24 * 60 + all_day_time.hour * 60 + all_day_time.minute)


def duration(minutes: int) -> str:
    """-1440 -> '-P1D', -180 -> '-PT3H', 540 -> 'PT9H'."""
    sign = "-" if minutes < 0 else ""
    days, rest = divmod(abs(minutes), 24 * 60)
    hours, mins = divmod(rest, 60)
    text = "P" + (f"{days}D" if days else "")
    if hours or mins or not days:
        text += "T" + (f"{hours}H" if hours else "") + (f"{mins}M" if mins or not hours else "")
    return sign + text


def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Regels van hooguit 75 bytes; vervolgregels beginnen met een spatie. Nooit midden in een teken."""
    out, current, limit = [], b"", 75
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > limit:
            out.append(current.decode("utf-8"))
            current, limit = b" ", 75
        current += encoded
    out.append(current.decode("utf-8"))
    return "\r\n".join(out)
