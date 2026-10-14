"""Events of Sunny: its settings and the nicknames it learned. The conversation itself isn't in the event store."""
import datetime as dt
from dataclasses import dataclass, field


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class AssistantSettingsChanged:
    """The model Sunny uses (an ID from models.MODELS), in the stream "assistant-settings"."""
    model: str
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class QuickSavesChanged:
    """Chapter updates saved right away (with Undo) or only after Confirm; in "assistant-settings"."""
    enabled: bool
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class AliasAdded:
    """A nickname: how a title was heard ("solo leaving"), for the series it means. In "assistant-aliases"."""
    heard: str
    series_id: str
    at: dt.datetime = field(default_factory=now)


@dataclass(frozen=True)
class AliasRemoved:
    heard: str
    at: dt.datetime = field(default_factory=now)


EVENT_TYPES = [AssistantSettingsChanged, QuickSavesChanged, AliasAdded, AliasRemoved]
