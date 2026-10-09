import datetime as dt

from ..domain import DomainError
from .events import (
    PlanAdded, PlanDone, PlanEdited, PlanRemoved, PlanReopened, PlanRescheduled, PlanSkipped,
    ReminderSettingsChanged, Repeat,
)
from .schedule import occurs_on, with_weekdays


class Plan:
    """Eén afspraak of plan, opgebouwd uit zijn events."""

    def __init__(self, events: list):
        self.plan_id = None
        self.title = None
        self.day: dt.date | None = None
        self.time: dt.time | None = None
        self.duration_min: int | None = None
        self.note = ""
        self.repeat: Repeat | None = None
        self.done: set[dt.date] = set()
        self.skipped: set[dt.date] = set()
        self.removed = False
        for event in events:
            self._apply(event)

    @property
    def exists(self) -> bool:
        return self.plan_id is not None

    # ---- Toestand opbouwen uit events ----

    def _apply(self, event) -> None:
        if isinstance(event, PlanAdded):
            self.plan_id, self.title, self.note = event.plan_id, event.title, event.note
            self.day, self.time, self.duration_min, self.repeat = event.day, event.time, event.duration_min, event.repeat
        elif isinstance(event, PlanRescheduled):
            self.day, self.time = event.day, event.time
        elif isinstance(event, PlanEdited):
            self.title, self.note, self.duration_min, self.repeat = event.title, event.note, event.duration_min, event.repeat
        elif isinstance(event, PlanDone):
            self.done.add(event.on)
        elif isinstance(event, PlanReopened):
            self.done.discard(event.on)
        elif isinstance(event, PlanSkipped):
            self.skipped.add(event.on)
        elif isinstance(event, PlanRemoved):
            self.removed = True

    # ---- Beslissingen: regels checken, nieuwe events teruggeven ----

    def add(self, plan_id: str, title: str, day: dt.date | None, time: dt.time | None,
            duration_min: int | None, note: str, repeat: Repeat | None) -> list:
        if self.exists:
            raise DomainError("Dit plan bestaat al")
        title = _title(title)
        _check_when(day, time)
        repeat = _checked_repeat(day, repeat)
        return self._record(PlanAdded(plan_id, title, day, time, _duration(duration_min), note.strip(), repeat))

    def reschedule(self, day: dt.date | None, time: dt.time | None) -> list:
        self._require_active()
        _check_when(day, time)
        if day is None and self.repeat is not None:
            raise DomainError("Een herhaling heeft een datum nodig")
        if self.repeat is not None:
            _checked_repeat(day, self.repeat)  # de einddatum mag niet vóór de nieuwe begindatum liggen
        if (day, time) == (self.day, self.time):
            return []  # niets veranderd, dus ook geen event
        return self._record(PlanRescheduled(self.plan_id, day, time))

    def edit(self, title: str, note: str, duration_min: int | None, repeat: Repeat | None) -> list:
        self._require_active()
        title, note, duration_min = _title(title), note.strip(), _duration(duration_min)
        repeat = _checked_repeat(self.day, repeat)
        if (title, note, duration_min, repeat) == (self.title, self.note, self.duration_min, self.repeat):
            return []
        return self._record(PlanEdited(self.plan_id, title, note, duration_min, repeat))

    def mark_done(self, on: dt.date) -> list:
        self._require_active()
        if self.day is None:
            if self.done:  # Someday: af op de dag dat je hem afvinkt, maar maar één keer
                raise DomainError(f"'{self.title}' is al af")
        else:
            self._require_occurrence(on)
            if on in self.done:
                raise DomainError(f"'{self.title}' is al af")
        return self._record(PlanDone(self.plan_id, on))

    def reopen(self, on: dt.date) -> list:
        self._require_active()
        if on not in self.done:
            raise DomainError(f"'{self.title}' is niet af")
        return self._record(PlanReopened(self.plan_id, on))

    def skip(self, on: dt.date) -> list:
        self._require_active()
        if self.repeat is None:
            raise DomainError("Alleen een herhaling kun je één keer overslaan")
        self._require_occurrence(on)
        if on in self.skipped:
            raise DomainError("Die keer is al overgeslagen")
        return self._record(PlanSkipped(self.plan_id, on))

    def remove(self) -> list:
        self._require_active()
        return self._record(PlanRemoved(self.plan_id, self.title))

    def _require_active(self) -> None:
        if not self.exists:
            raise DomainError("Onbekend plan")
        if self.removed:
            raise DomainError(f"'{self.title}' is verwijderd")

    def _require_occurrence(self, on: dt.date) -> None:
        if not occurs_on(self.day, self.repeat, on) or on in self.skipped:
            raise DomainError(f"'{self.title}' valt niet op {on:%d-%m-%Y}")

    def _record(self, event) -> list:
        self._apply(event)
        return [event]


def _title(title: str) -> str:
    title = title.strip()
    if not title:
        raise DomainError("Een plan heeft een titel nodig")
    return title


def _check_when(day: dt.date | None, time: dt.time | None) -> None:
    if time is not None and day is None:
        raise DomainError("Een tijd zonder datum kan niet: kies ook een dag")


def _duration(minutes: int | None) -> int | None:
    if minutes is not None and minutes <= 0:
        raise DomainError("Een duur moet langer dan 0 minuten zijn")
    return minutes or None


def _checked_repeat(day: dt.date | None, repeat: Repeat | None) -> Repeat | None:
    if repeat is None:
        return None
    if day is None:
        raise DomainError("Een herhaling heeft een datum nodig")
    if any(not 0 <= d <= 6 for d in repeat.weekdays):
        raise DomainError("Onbekende weekdag")
    if repeat.until is not None and repeat.until < day:
        raise DomainError("Een herhaling kan niet eindigen voordat hij begint")
    return with_weekdays(day, Repeat(repeat.every, tuple(sorted(set(repeat.weekdays))), repeat.until))


class ReminderSettings:
    """De instelling voor herinneringen: één voor alle plannen, in de stream "planner-settings"."""

    STREAM = "planner-settings"
    # Standaard: 1 dag en 3 uur van tevoren, plannen van de hele dag om 9:00.
    DEFAULT = (True, 24 * 60, True, 3 * 60, dt.time(9))
    MAX_MINUTES = 14 * 24 * 60

    def __init__(self, events: list):
        self.current = self.DEFAULT
        for event in events:
            if isinstance(event, ReminderSettingsChanged):
                self.current = (event.first_enabled, event.first_minutes, event.second_enabled,
                                event.second_minutes, event.all_day_time)

    def change(self, first_enabled: bool, first_minutes: int, second_enabled: bool, second_minutes: int,
               all_day_time: dt.time) -> list:
        for minutes in (first_minutes, second_minutes):
            if not 0 < minutes <= self.MAX_MINUTES:
                raise DomainError("Een herinnering moet tussen 1 minuut en 14 dagen van tevoren zijn")
        new = (bool(first_enabled), first_minutes, bool(second_enabled), second_minutes, all_day_time)
        if new == self.current:
            return []  # dezelfde instelling: geen event
        self.current = new
        return [ReminderSettingsChanged(*new)]
