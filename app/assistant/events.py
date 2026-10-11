"""Events van Sunny. Alleen de instelling: het gesprek zelf staat niet in de event store."""
import datetime as dt
from dataclasses import dataclass, field


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class AssistantSettingsChanged:
    """Het model dat Sunny gebruikt (een ID uit models.MODELS), in de stream "assistant-settings"."""
    model: str
    at: dt.datetime = field(default_factory=now)


EVENT_TYPES = [AssistantSettingsChanged]
