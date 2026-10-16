import datetime as dt
from dataclasses import dataclass

from .. import clock
from ..eventstore import EventStore
from .aggregate import GymDay, StepsDay, gym_stream, steps_stream


# ---- Commands: what you want to happen ----

@dataclass(frozen=True)
class RecordSteps:
    day: dt.date
    steps: int


@dataclass(frozen=True)
class ArriveAtGym:
    arrived_at: dt.datetime  # local time


@dataclass(frozen=True)
class LeaveGym:
    left_at: dt.datetime     # local time


# ---- The handler: load, decide, save ----

class FitnessCommandHandler:
    def __init__(self, store: EventStore):
        self._store = store

    def handle(self, command) -> list:
        if isinstance(command, RecordSteps):
            aggregate = self._steps(command.day)
            events = aggregate.record(command.steps, clock.today())
        elif isinstance(command, ArriveAtGym):
            aggregate = self._gym(command.arrived_at.date())
            events = aggregate.arrive(command.arrived_at, clock.local_now())
        elif isinstance(command, LeaveGym):
            aggregate = self._gym(command.left_at.date())
            events = aggregate.leave(command.left_at, clock.local_now())
        else:
            raise TypeError(f"Unknown command: {type(command).__name__}")
        if events:
            self._store.append(aggregate.stream_id, events)
        return events

    def handle_many(self, commands: list) -> list[list]:
        """All or nothing: if one command is refused, none of the others is saved."""
        with self._store.transaction():
            return [self.handle(command) for command in commands]

    def _steps(self, day: dt.date) -> StepsDay:
        return StepsDay(day, self._store.load_stream(steps_stream(day)))

    def _gym(self, day: dt.date) -> GymDay:
        return GymDay(day, self._store.load_stream(gym_stream(day)))
