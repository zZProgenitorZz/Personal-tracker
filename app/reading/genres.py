"""De vaste lijst met genres, en het vertalen van genres uit coverbronnen naar die lijst.

Een genre toevoegen of weghalen: pas GENRES aan (alfabetisch). Bestaande
events met een genre dat niet meer in de lijst staat, blijven leesbaar; je kunt
het alleen niet meer opnieuw kiezen.
"""
import re

GENRES = [
    "Action", "Adventure", "Comedy", "Cultivation", "Drama", "Fantasy", "Game",
    "Historical", "Horror", "Isekai", "Martial Arts", "Mystery", "Psychological",
    "Regression", "Reincarnation", "Romance", "School Life", "Sci-Fi", "Slice of Life",
    "Sports", "Supernatural", "System", "Thriller", "Tragedy",
]

# Andere namen die bronnen gebruiken (alles in kleine letters).
_ALIASES = {g.lower(): g for g in GENRES} | {
    "science fiction": "Sci-Fi", "sci fi": "Sci-Fi", "scifi": "Sci-Fi",
    "school": "School Life",
    "murim": "Martial Arts", "wuxia": "Martial Arts",
    "xianxia": "Cultivation", "xuanhuan": "Cultivation",
    "video games": "Game", "virtual world": "Game", "litrpg": "Game", "gamelit": "Game",
    "history": "Historical",
    "regressor": "Regression", "returner": "Regression",
}


def _candidates(name: str) -> list[str]:
    """'Fiction / Action & Adventure' -> ['fiction', 'action', 'adventure']; 'Fantasy fiction' -> 'fantasy'."""
    parts = re.split(r"\s*(?:/|&|,|\band\b)\s*", name.lower())
    found = []
    for part in [name.lower(), *parts]:
        part = part.strip()
        found += [part, re.sub(r"\s+fiction$", "", part)]
    return found


def from_source(names) -> list[str]:
    """Genres van een bron vertalen naar de vaste lijst; wat niet past, valt weg."""
    found = {_ALIASES[c] for name in names if isinstance(name, str) for c in _candidates(name) if c in _ALIASES}
    return sorted(found, key=GENRES.index)


def validated(genres) -> list[str]:
    """Zelf gekozen genres: alleen uit de lijst, op volgorde en zonder dubbele.
    Gooit ValueError met de onbekende genres."""
    unknown = [g for g in genres if g not in GENRES]
    if unknown:
        raise ValueError(", ".join(unknown))
    return sorted(set(genres), key=GENRES.index)
