"""De vaste genrelijst voor Watching (films, series en anime). Aanpassen: alfabetisch houden."""
from ..genres import GenreSet

WATCH_GENRES = GenreSet([
    "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family",
    "Fantasy", "Historical", "Horror", "Isekai", "Martial Arts", "Music", "Mystery",
    "Psychological", "Romance", "School Life", "Sci-Fi", "Slice of Life", "Sports",
    "Supernatural", "Thriller", "War",
], {
    "science fiction": "Sci-Fi", "sci fi": "Sci-Fi", "scifi": "Sci-Fi", "mecha": "Sci-Fi",
    "history": "Historical", "school": "School Life", "kids": "Family",
    "suspense": "Thriller", "espionage": "Thriller", "musical": "Music",
})
