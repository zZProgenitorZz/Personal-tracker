import logging

from ..domain import DomainError
from .events import AliasAdded, AliasRemoved, AssistantSettingsChanged, QuickSavesChanged
from .models import DEFAULT_MODEL, model_by_id
from .quick import sound_key

log = logging.getLogger("progen.assistant")
# The settings are rebuilt on every message; warn about a missing model only once per process.
_warned_model: str | None = None


class AssistantSettings:
    """Sunny's setting (which model), like ReminderSettings for the planner: its own stream,
    so the choice is in backups and stays the same after a restart. Without an event: the default model."""

    STREAM = "assistant-settings"

    def __init__(self, events: list):
        self.stored = DEFAULT_MODEL
        self.quick_saves = True  # chapter updates right away, with Undo (Settings › Sunny)
        for event in events:
            if isinstance(event, AssistantSettingsChanged):
                self.stored = event.model
            elif isinstance(event, QuickSavesChanged):
                self.quick_saves = event.enabled
        if model_by_id(self.stored) is None:
            _warn_missing(self.stored)

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

    def change_quick_saves(self, enabled: bool) -> list:
        if bool(enabled) == self.quick_saves:
            return []
        self.quick_saves = bool(enabled)
        return [QuickSavesChanged(bool(enabled))]


class Aliases:
    """Nicknames for series, by how they sound (sound_key), in the stream "assistant-aliases"."""

    STREAM = "assistant-aliases"

    def __init__(self, events: list):
        self.by_key: dict[str, tuple[str, str]] = {}  # sound key -> (heard, series_id)
        for event in events:
            if isinstance(event, AliasAdded):
                self.by_key[sound_key(event.heard)] = (event.heard, event.series_id)
            elif isinstance(event, AliasRemoved):
                self.by_key.pop(sound_key(event.heard), None)

    def add(self, heard: str, series_id: str) -> list:
        heard = heard.strip()
        if not sound_key(heard):
            raise DomainError("A nickname needs some letters")
        if self.by_key.get(sound_key(heard), (None, None))[1] == series_id:
            return []  # already known
        self.by_key[sound_key(heard)] = (heard, series_id)
        return [AliasAdded(heard, series_id)]

    def remove(self, heard: str) -> list:
        if sound_key(heard) not in self.by_key:
            raise DomainError(f"Unknown nickname: {heard}")
        del self.by_key[sound_key(heard)]
        return [AliasRemoved(heard)]


def _warn_missing(model: str) -> None:
    global _warned_model
    if model != _warned_model:
        _warned_model = model
        log.warning("Sunny: model %r is not in the list (anymore); using %s instead", model, DEFAULT_MODEL)
