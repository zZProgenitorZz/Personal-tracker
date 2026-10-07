import uuid
from dataclasses import dataclass

from ..domain import DomainError
from ..eventstore import EventStore
from .aggregate import WatchItem
from .events import WatchKind, WatchStatus
from .projections import WatchlistProjection


# ---- Commands: wat je wílt dat er gebeurt ----

@dataclass(frozen=True)
class AddShow:
    title: str
    kind: WatchKind
    status: WatchStatus = WatchStatus.WATCHING
    cover: str | None = None
    genres: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangeShowStatus:
    show_id: str
    new_status: WatchStatus


@dataclass(frozen=True)
class SetShowGenres:
    show_id: str
    genres: tuple[str, ...]


@dataclass(frozen=True)
class RemoveShow:
    show_id: str


# ---- De handler: laden, beslissen, opslaan ----

class WatchingCommandHandler:
    def __init__(self, store: EventStore, watchlist: WatchlistProjection):
        self._store = store
        self._watchlist = watchlist

    def handle(self, command) -> list:
        if isinstance(command, AddShow):
            title = command.title.strip()
            # Uniek per soort: Dune de film en Dune de serie mogen allebei.
            if self._watchlist.find(title, command.kind):
                raise DomainError(f"'{title}' staat al in je lijst")
            item = WatchItem([])
            events = item.add(str(uuid.uuid4()), title, command.kind, command.status, command.cover, command.genres)
        elif isinstance(command, ChangeShowStatus):
            item = self._load(command.show_id)
            events = item.change_status(command.new_status)
        elif isinstance(command, SetShowGenres):
            item = self._load(command.show_id)
            events = item.set_genres(command.genres)
        elif isinstance(command, RemoveShow):
            item = self._load(command.show_id)
            events = item.remove()
        else:
            raise TypeError(f"Onbekend command: {type(command).__name__}")

        self._store.append(item.show_id, events)
        return events

    def _load(self, show_id: str) -> WatchItem:
        return WatchItem(self._store.load_stream(show_id))
