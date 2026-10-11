import logging

from ..domain import DomainError
from .events import AssistantSettingsChanged
from .models import DEFAULT_MODEL, model_by_id

log = logging.getLogger("progen.assistant")


class AssistantSettings:
    """Sunny's instelling (welk model), net als ReminderSettings bij de planner: één eigen stream,
    zodat de keuze in back-ups zit en na herstarten hetzelfde is. Zonder event: het standaardmodel."""

    STREAM = "assistant-settings"

    def __init__(self, events: list):
        self.stored = DEFAULT_MODEL
        for event in events:
            if isinstance(event, AssistantSettingsChanged):
                self.stored = event.model

    @property
    def model(self) -> str:
        """Het model om te gebruiken. Staat het opgeslagen model niet (meer) in de lijst: de standaard."""
        if model_by_id(self.stored) is None:
            log.warning("Sunny: model %r is not in the list (anymore); using %s instead", self.stored, DEFAULT_MODEL)
            return DEFAULT_MODEL
        return self.stored

    def change(self, model: str) -> list:
        if model_by_id(model) is None:
            raise DomainError(f"Onbekend model: {model}")
        if model == self.model:
            return []  # hetzelfde model: geen event
        self.stored = model
        return [AssistantSettingsChanged(model)]
