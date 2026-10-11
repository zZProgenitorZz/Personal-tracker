import logging

from ..domain import DomainError
from .events import AssistantSettingsChanged
from .models import DEFAULT_MODEL, model_by_id

log = logging.getLogger("progen.assistant")


class AssistantSettings:
    """Sunny's setting (which model), like ReminderSettings for the planner: its own stream,
    so the choice is in backups and stays the same after a restart. Without an event: the default model."""

    STREAM = "assistant-settings"

    def __init__(self, events: list):
        self.stored = DEFAULT_MODEL
        for event in events:
            if isinstance(event, AssistantSettingsChanged):
                self.stored = event.model
        if model_by_id(self.stored) is None:  # report once, not on every read
            log.warning("Sunny: model %r is not in the list (anymore); using %s instead", self.stored, DEFAULT_MODEL)

    @property
    def model(self) -> str:
        """The model to use. If the stored model isn't (or is no longer) in the list: the default."""
        return self.stored if model_by_id(self.stored) else DEFAULT_MODEL

    def change(self, model: str) -> list:
        if model_by_id(model) is None:
            raise DomainError(f"Unknown model: {model}")
        if model == self.model:
            return []  # the same model: no event
        self.stored = model
        return [AssistantSettingsChanged(model)]
