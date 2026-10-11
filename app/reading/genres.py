"""The fixed genre list for Reading (novels and manhwa).

To add or remove a genre: change GENRES (alphabetical). Existing
events with a genre that is no longer in the list stay readable; you just
can't pick it again.
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
