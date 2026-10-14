from dataclasses import dataclass

from ..eventstore import EventStore
from .aggregate import Aliases, AssistantSettings


@dataclass(frozen=True)
class ChangeAssistantModel:
    model: str


@dataclass(frozen=True)
class ChangeQuickSaves:
    enabled: bool


@dataclass(frozen=True)
class AddAlias:
    heard: str
    series_id: str


@dataclass(frozen=True)
class RemoveAlias:
    heard: str


class AssistantCommandHandler:
    def __init__(self, store: EventStore):
        self._store = store

    def handle(self, command) -> list:
        if isinstance(command, ChangeAssistantModel):
            return self._save(AssistantSettings, lambda settings: settings.change(command.model))
        if isinstance(command, ChangeQuickSaves):
            return self._save(AssistantSettings, lambda settings: settings.change_quick_saves(command.enabled))
        if isinstance(command, AddAlias):
            return self._save(Aliases, lambda aliases: aliases.add(command.heard, command.series_id))
        if isinstance(command, RemoveAlias):
            return self._save(Aliases, lambda aliases: aliases.remove(command.heard))
        raise TypeError(f"Unknown command: {type(command).__name__}")

    def _save(self, kind, decide) -> list:
        """Load the stream, let the aggregate decide, save the events (load → decide → save)."""
        events = decide(kind(self._store.load_stream(kind.STREAM)))
        if events:
            self._store.append(kind.STREAM, events)
        return events

    def settings(self) -> AssistantSettings:
        """The current setting, fresh from the store: a change applies right away."""
        return AssistantSettings(self._store.load_stream(AssistantSettings.STREAM))
