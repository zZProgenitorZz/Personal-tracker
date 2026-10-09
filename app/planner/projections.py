import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from .aggregate import ReminderSettings
from .events import (
    PlanAdded, PlanDone, PlanEdited, PlanRemoved, PlanReopened, PlanRescheduled, PlanSkipped,
    ReminderSettingsChanged, Repeat,
)
from .schedule import occurs_on


@dataclass
class PlanEntry:
    plan_id: str
    title: str
    day: dt.date | None
    time: dt.time | None
    duration_min: int | None
    note: str
    repeat: Repeat | None
    added_at: dt.datetime
    done: set[dt.date] = field(default_factory=set)
    skipped: set[dt.date] = field(default_factory=set)


@dataclass(frozen=True)
class Occurrence:
    """Eén keer dat een plan valt (bij een herhaling: één van de keren)."""
    plan: PlanEntry
    on: dt.date

    @property
    def title(self) -> str:
        return self.plan.title

    @property
    def time(self) -> dt.time | None:
        return self.plan.time

    @property
    def done(self) -> bool:
        return self.on in self.plan.done

    @property
    def is_repeat(self) -> bool:
        return self.plan.repeat is not None

    @property
    def starts_at(self) -> dt.datetime | None:
        return dt.datetime.combine(self.on, self.time) if self.time else None

    @property
    def ends_at(self) -> dt.datetime | None:
        if not self.time or not self.plan.duration_min:
            return None
        return self.starts_at + dt.timedelta(minutes=self.plan.duration_min)


@dataclass(frozen=True)
class Reminder:
    key: str            # plan/voorkomen/herinnering: zo meldt de launcher niets dubbel
    plan_id: str
    on: dt.date
    fires_at: dt.datetime
    text: str           # "Tomorrow: Dentist at 14:00"


def _sort_key(o: Occurrence):
    return (o.time is not None, o.time or dt.time(0), o.title.lower())  # hele dag eerst, dan op tijd


def lead_text(minutes: int) -> str:
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return "Tomorrow" if days == 1 else f"In {days} days"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"In {hours} hour{'s' if hours != 1 else ''}"
    return f"In {minutes} minute{'s' if minutes != 1 else ''}"


class AgendaProjection:
    """Alle plannen. Herhalingen worden per dag uitgerekend; overgeslagen keren vallen weg.
    Houdt ook de instelling voor herinneringen bij (ReminderSettingsChanged)."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._plans: dict[str, PlanEntry] = {}
        self.reminder_settings = ReminderSettings.DEFAULT

    def apply(self, event) -> None:
        if isinstance(event, PlanAdded):
            self._plans[event.plan_id] = PlanEntry(event.plan_id, event.title, event.day, event.time,
                                                   event.duration_min, event.note, event.repeat, event.at)
        elif isinstance(event, ReminderSettingsChanged):
            self.reminder_settings = (event.first_enabled, event.first_minutes, event.second_enabled,
                                      event.second_minutes, event.all_day_time)
        elif event_plan := self._plans.get(getattr(event, "plan_id", None)):
            if isinstance(event, PlanRescheduled):
                event_plan.day, event_plan.time = event.day, event.time
            elif isinstance(event, PlanEdited):
                event_plan.title, event_plan.note = event.title, event.note
                event_plan.duration_min, event_plan.repeat = event.duration_min, event.repeat
            elif isinstance(event, PlanDone):
                event_plan.done.add(event.on)
            elif isinstance(event, PlanReopened):
                event_plan.done.discard(event.on)
            elif isinstance(event, PlanSkipped):
                event_plan.skipped.add(event.on)
            elif isinstance(event, PlanRemoved):
                del self._plans[event.plan_id]

    # ---- Lezen ----

    def get(self, plan_id: str) -> PlanEntry | None:
        return self._plans.get(plan_id)

    def all(self) -> list[PlanEntry]:
        return sorted(self._plans.values(), key=lambda p: p.added_at)

    def day(self, day: dt.date) -> list[Occurrence]:
        found = [Occurrence(p, day) for p in self._plans.values()
                 if occurs_on(p.day, p.repeat, day) and day not in p.skipped]
        return sorted(found, key=_sort_key)

    def week(self, monday: dt.date) -> list[tuple[dt.date, list[Occurrence]]]:
        days = [monday + dt.timedelta(days=i) for i in range(7)]
        return [(d, self.day(d)) for d in days]

    def someday(self) -> list[PlanEntry]:
        return [p for p in self.all() if p.day is None and not p.done]

    def due(self, now: dt.datetime, window: dt.timedelta) -> list[Reminder]:
        """Herinneringen die vielen in (now - window, now]. `now` is lokale tijd zonder tijdzone."""
        first_on, first_min, second_on, second_min, all_day_time = self.reminder_settings
        leads = [(n, minutes) for n, (on, minutes) in enumerate([(first_on, first_min), (second_on, second_min)], 1)
                 if on]
        if not leads:
            return []
        start = (now - window).date()
        end = (now + dt.timedelta(minutes=max(m for _, m in leads)) + dt.timedelta(days=1)).date()
        found, seen = [], set()
        day = start
        while day <= end:
            for occurrence in self.day(day):
                if occurrence.done:
                    continue
                for number, minutes in leads:
                    fires_at, text = self._reminder(occurrence, minutes, all_day_time)
                    moment = (occurrence.plan.plan_id, occurrence.on, fires_at)
                    if now - window < fires_at <= now and moment not in seen:
                        seen.add(moment)
                        found.append(Reminder(f"{occurrence.plan.plan_id}/{occurrence.on.isoformat()}/{number}",
                                              occurrence.plan.plan_id, occurrence.on, fires_at, text))
            day += dt.timedelta(days=1)
        return sorted(found, key=lambda r: (r.fires_at, r.key))

    @staticmethod
    def _reminder(o: Occurrence, minutes: int, all_day_time: dt.time) -> tuple[dt.datetime, str]:
        if o.time is None:
            # Hele dag: een dag of meer van tevoren = op die dag om all_day_time; korter = die dag zelf.
            days = minutes // (24 * 60)
            fires_on = o.on - dt.timedelta(days=days)
            lead = "Today" if days == 0 else ("Tomorrow" if days == 1 else f"In {days} days")
            return dt.datetime.combine(fires_on, all_day_time), f"{lead}: {o.title}"
        fires_at = o.starts_at - dt.timedelta(minutes=minutes)
        return fires_at, f"{lead_text(minutes)}: {o.title} at {o.time:%H:%M}"


class PlannerActivityProjection:
    """Hoe vaak een plan is verzet. Nu alleen bewaard; later bruikbaar voor inzichten."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._rescheduled: Counter = Counter()

    def apply(self, event) -> None:
        if isinstance(event, PlanRescheduled):
            self._rescheduled[event.plan_id] += 1

    def rescheduled(self, plan_id: str) -> int:
        return self._rescheduled[plan_id]
