"""De vaste genrelijst voor Reading (novels en manhwa).

Een genre toevoegen of weghalen: pas GENRES aan (alfabetisch). Bestaande
events met een genre dat niet meer in de lijst staat, blijven leesbaar; je kunt
het alleen niet meer opnieuw kiezen.
"""
from ..genres import GenreSet

GENRES = [
    "Action", "Adventure", "Comedy", "Cultivation", "Drama", "Fantasy", "Game",
    "Historical", "Horror", "Isekai", "Martial Arts", "Mystery", "Psychological",
    "Regression", "Reincarnation", "Romance", "School Life", "Sci-Fi", "Slice of Life",
    "Sports", "Supernatural", "System", "Thriller", "Tragedy",
]

READING_GENRES = GenreSet(GENRES, {
    "science fiction": "Sci-Fi", "sci fi": "Sci-Fi", "scifi": "Sci-Fi",
    "school": "School Life",
    "murim": "Martial Arts", "wuxia": "Martial Arts",
    "xianxia": "Cultivation", "xuanhuan": "Cultivation",
    "video games": "Game", "video game": "Game", "virtual world": "Game", "litrpg": "Game", "gamelit": "Game",
    "history": "Historical",
    "regressor": "Regression", "returner": "Regression",
})

from_source = READING_GENRES.from_source
validated = READING_GENRES.validated
