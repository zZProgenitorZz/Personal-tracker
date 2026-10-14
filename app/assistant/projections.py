"""Sunny's read models: the nicknames it learned, for quick_log and find_item."""
from .events import AliasAdded, AliasRemoved
from .quick import sound_key


class AliasProjection:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._by_key: dict[str, tuple[str, str]] = {}  # sound key -> (heard, series_id)

    def apply(self, event) -> None:
        if isinstance(event, AliasAdded):
            self._by_key[sound_key(event.heard)] = (event.heard, event.series_id)
        elif isinstance(event, AliasRemoved):
            self._by_key.pop(sound_key(event.heard), None)

    def series_for(self, heard: str) -> str | None:
        """The series a heard title is a nickname for (it has to sound the same), or None."""
        found = self._by_key.get(sound_key(heard))
        return found[1] if found else None

    def all(self) -> list[tuple[str, str]]:
        """(heard, series_id), alphabetical."""
        return sorted(self._by_key.values(), key=lambda pair: pair[0].lower())
