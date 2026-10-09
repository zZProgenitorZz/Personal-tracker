import datetime as dt
import uuid
from dataclasses import dataclass

from ..eventstore import EventStore
from .aggregate import Plan, ReminderSettings
from .events import Repeat


# ---- Commands: wat je wílt dat er gebeurt ----

@dataclass(frozen=True)
class AddPlan:
    title: str
    day: dt.date | None = None
    time: dt.time | None = None
    duration_min: int | None = None
    note: str = ""
    repeat: Repeat | None = None


@dataclass(frozen=True)
class ReschedulePlan:
    plan_id: str
    day: dt.date | None
    time: dt.time | None


@dataclass(frozen=True)
class EditPlan:
    plan_id: str
    title: str
    note: str
    duration_min: int | None
    repeat: Repeat | None


@dataclass(frozen=True)
class MarkPlanDone:
    plan_id: str
    on: dt.date


@dataclass(frozen=True)
class ReopenPlan:
    plan_id: str
    on: dt.date


@dataclass(frozen=True)
class SkipPlan:
    plan_id: str
    on: dt.date


@dataclass(frozen=True)
class RemovePlan:
    plan_id: str


@dataclass(frozen=True)
class ChangeReminderSettings:
    first_enabled: bool
    first_minutes: int
    second_enabled: bool
    second_minutes: int
    all_day_time: dt.time


# ---- De handler: laden, beslissen, opslaan ----

class PlannerCommandHandler:
    def __init__(self, store: EventStore):
        self._store = store

    def handle(self, command) -> list:
        if isinstance(command, ChangeReminderSettings):
            settings = ReminderSettings(self._store.load_stream(ReminderSettings.STREAM))
            events = settings.change(command.first_enabled, command.first_minutes, command.second_enabled,
                                     command.second_minutes, command.all_day_time)
            self._store.append(ReminderSettings.STREAM, events)
            return events

        if isinstance(command, AddPlan):
            plan = Plan([])
            events = plan.add(str(uuid.uuid4()), command.title, command.day, command.time,
                              command.duration_min, command.note, command.repeat)
        else:
            plan = self._load(command.plan_id)
            if isinstance(command, ReschedulePlan):
                events = plan.reschedule(command.day, command.time)
            elif isinstance(command, EditPlan):
                events = plan.edit(command.title, command.note, command.duration_min, command.repeat)
            elif isinstance(command, MarkPlanDone):
                events = plan.mark_done(command.on)
            elif isinstance(command, ReopenPlan):
                events = plan.reopen(command.on)
            elif isinstance(command, SkipPlan):
                events = plan.skip(command.on)
            elif isinstance(command, RemovePlan):
                events = plan.remove()
            else:
                raise TypeError(f"Onbekend command: {type(command).__name__}")

        if events:
            self._store.append(plan.plan_id, events)
        return events

    def _load(self, plan_id: str) -> Plan:
        return Plan(self._store.load_stream(plan_id))
