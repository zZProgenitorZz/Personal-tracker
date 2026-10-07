"""Een vaste genrelijst per tracker, en genres uit externe bronnen daarnaar vertalen.

Elke tracker maakt zijn eigen GenreSet (zie reading/genres.py en watching/genres.py).
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
        assert names == sorted(set(names)), "genres alfabetisch en zonder dubbele"
        self.names = names
        # Andere namen die bronnen gebruiken (in kleine letters) -> een genre uit de lijst.
        self._aliases = {g.lower(): g for g in names} | aliases

    def from_source(self, names) -> list[str]:
        """Genres van een bron vertalen naar de vaste lijst; wat niet past, valt weg."""
        found = {self._aliases[c] for name in names if isinstance(name, str)
                 for c in _candidates(name) if c in self._aliases}
        return sorted(found, key=self.names.index)

    def validated(self, genres) -> list[str]:
        """Zelf gekozen genres: alleen uit de lijst, op volgorde en zonder dubbele.
        Gooit ValueError met de onbekende genres."""
        unknown = [g for g in genres if g not in self.names]
        if unknown:
            raise ValueError(", ".join(unknown))
        return sorted(set(genres), key=self.names.index)
