import uuid
from dataclasses import dataclass

from ..eventstore import EventStore
from .events import Kind, Status
from .aggregate import DomainError, ReadingSeries
from .projections import LibraryProjection


# ---- Commands: wat je wílt dat er gebeurt ----

@dataclass(frozen=True)
class StartSeries:
    title: str
    kind: Kind
    source: str
    start_chapter: float = 0


@dataclass(frozen=True)
class LogProgress:
    series_id: str
    chapter: float


@dataclass(frozen=True)
class ChangeStatus:
    series_id: str
    new_status: Status


# ---- De handler: laden, beslissen, opslaan ----

class ReadingCommandHandler:
    def __init__(self, store: EventStore, library: LibraryProjection):
        self._store = store
        self._library = library

    def handle(self, command) -> list:
        if isinstance(command, StartSeries):
            if self._library.find_by_title(command.title):
                raise DomainError(f"'{command.title.strip()}' staat al in je bibliotheek")
            series = ReadingSeries([])
            events = series.start(
                str(uuid.uuid4()),
                command.title.strip(),
                command.kind,
                command.source,
                command.start_chapter,
            )
        elif isinstance(command, LogProgress):
            series = self._load(command.series_id)
            events = series.log_progress(command.chapter)
        elif isinstance(command, ChangeStatus):
            series = self._load(command.series_id)
            events = series.change_status(command.new_status)
        else:
            raise TypeError(f"Onbekend command: {type(command).__name__}")

        self._store.append(series.series_id, events)
        return events

    def _load(self, series_id: str) -> ReadingSeries:
        return ReadingSeries(self._store.load_stream(series_id))