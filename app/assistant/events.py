"""Events of Sunny. Only the setting: the conversation itself isn't in the event store."""
import datetime as dt
from dataclasses import dataclass, field


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class AssistantSettingsChanged:
    """The model Sunny uses (an ID from models.MODELS), in the stream "assistant-settings"."""
    model: str
    at: dt.datetime = field(default_factory=now)


EVENT_TYPES = [AssistantSettingsChanged]
