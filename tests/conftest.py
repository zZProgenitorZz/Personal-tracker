import pytest

from app.reading import cover_sources


@pytest.fixture(autouse=True)
def fresh_jikan_state(monkeypatch):
    """MyAnimeList (Jikan) remembers per process when the last request was and whether the
    source is being skipped for a while. Each test starts clean, whatever the order."""
    monkeypatch.setattr(cover_sources, "_jikan_last", [0.0])
    monkeypatch.setattr(cover_sources, "_jikan_down_until", [0.0])
