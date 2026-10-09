"""Events van Planner: afspraken en plannen. Klassenamen zijn uniek over alle domeinen heen,
want de event store slaat alleen de klassenaam op.

Datum en tijd van een plan zijn lokale tijd (de klok van deze pc), niet UTC zoals `at`
(wanneer Progen het vastlegde). Geen tijd = de hele dag; geen datum = "Someday".
"""
import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Frequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"  # op dezelfde dag van de maand; bij 31 in een korte maand de laatste dag


@dataclass(frozen=True)
class Repeat:
    every: Frequency
    weekdays: tuple[int, ...] = ()  # alleen bij WEEKLY: 0 = maandag ... 6 = zondag
    until: dt.date | None = None    # de laatste dag waarop het nog kan vallen


@dataclass(frozen=True)
class PlanAdded:
    plan_id: str
    title: str
    day: dt.date | None
    time: dt.time | None
    duration_min: int | None
    note: str
    repeat: Repeat | None
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanRescheduled:
    """Verzet, ingepland of terug naar Someday (day None)."""
    plan_id: str
    day: dt.date | None
    time: dt.time | None
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanEdited:
    """De volledige nieuwe titel, notitie, duur en herhaling."""
    plan_id: str
    title: str
    note: str
    duration_min: int | None
    repeat: Repeat | None
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanDone:
    plan_id: str
    on: dt.date  # bij een herhaling: welk voorkomen
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanReopened:
    plan_id: str
    on: dt.date
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanSkipped:
    """Eén keer van een herhaling overslaan."""
    plan_id: str
    on: dt.date
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class PlanRemoved:
    plan_id: str
    title: str
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class ReminderSettingsChanged:
    """De volledige nieuwe instelling voor herinneringen (één voor alle plannen), in een eigen stream."""
    first_enabled: bool
    first_minutes: int      # hoeveel minuten van tevoren
    second_enabled: bool
    second_minutes: int
    all_day_time: dt.time   # wanneer herinneringen voor plannen van de hele dag komen
    at: dt.datetime = field(default_factory=now)


EVENT_TYPES = [PlanAdded, PlanRescheduled, PlanEdited, PlanDone, PlanReopened, PlanSkipped, PlanRemoved,
               ReminderSettingsChanged]
