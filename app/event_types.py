"""Every event type in the shared event store (one database, one backup)."""
from .assistant.events import EVENT_TYPES as ASSISTANT
from .fitness.events import EVENT_TYPES as FITNESS
from .listening.events import EVENT_TYPES as LISTENING
from .planner.events import EVENT_TYPES as PLANNER
from .reading.events import EVENT_TYPES as READING
from .watching.events import EVENT_TYPES as WATCHING

ALL_EVENT_TYPES = [*READING, *WATCHING, *LISTENING, *PLANNER, *FITNESS, *ASSISTANT]
