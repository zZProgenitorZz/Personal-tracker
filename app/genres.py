"""A fixed genre list per tracker, and mapping genres from outside sources onto it.

Each tracker makes its own GenreSet (see reading/genres.py and watching/genres.py).
"""
import re


def _candidates(name: str) -> list[str]:
    """'Fiction / Action & Adventure' -> ['fiction', 'action', 'adventure']; 'Fantasy fiction' -> 'fantasy'."""
    parts = re.split(r"\s*(?:/|&|,|\band\b)\s*", name.lower())
    found = []
    for part in [name.lower(), *parts]:
        part = part.strip()
        found += [part, re.sub(r"\s+fiction$", "", part)]
    return found


class GenreSet:
    def __init__(self, names: list[str], aliases: dict[str, str]):
        assert names == sorted(set(names)), "genres in alphabetical order and without duplicates"
        self.names = names
        # Other names that sources use (lower case) -> a genre from the list.
        self._aliases = {g.lower(): g for g in names} | aliases

    def from_source(self, names) -> list[str]:
        """Maps a source's genres onto the fixed list; whatever doesn't fit is dropped."""
        found = {self._aliases[c] for name in names if isinstance(name, str)
                 for c in _candidates(name) if c in self._aliases}
        return sorted(found, key=self.names.index)

    def validated(self, genres) -> list[str]:
        """Genres you picked yourself: only from the list, in order and without duplicates.
        Raises ValueError with the unknown genres."""
        unknown = [g for g in genres if g not in self.names]
        if unknown:
            raise ValueError(", ".join(unknown))
        return sorted(set(genres), key=self.names.index)
