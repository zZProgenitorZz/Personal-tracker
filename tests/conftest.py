import pytest

from app.reading import cover_sources


@pytest.fixture(autouse=True)
def fresh_jikan_state(monkeypatch):
    """MyAnimeList (Jikan) onthoudt per proces wanneer het laatste verzoek was en of de
    bron even overgeslagen wordt. Elke test begint schoon, los van de volgorde."""
    monkeypatch.setattr(cover_sources, "_jikan_last", [0.0])
    monkeypatch.setattr(cover_sources, "_jikan_down_until", [0.0])
