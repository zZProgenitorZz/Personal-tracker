from dataclasses import dataclass

from ..eventstore import EventStore
from .aggregate import AssistantSettings


@dataclass(frozen=True)
class ChangeAssistantModel:
    model: str


class AssistantCommandHandler:
    def __init__(self, store: EventStore):
        self._store = store

    def handle(self, command) -> list:
        if not isinstance(command, ChangeAssistantModel):
            raise TypeError(f"Unknown command: {type(command).__name__}")
        settings = AssistantSettings(self._store.load_stream(AssistantSettings.STREAM))
        events = settings.change(command.model)
        if events:
            self._store.append(AssistantSettings.STREAM, events)
        return events

    def settings(self) -> AssistantSettings:
        """The current setting, fresh from the store: a change applies right away."""
        return AssistantSettings(self._store.load_stream(AssistantSettings.STREAM))
