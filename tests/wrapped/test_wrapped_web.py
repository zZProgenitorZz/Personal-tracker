"""Wrapped als pagina (#wrapped) en als kaart op het startscherm."""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

THIS_YEAR = date.today().year


@pytest.fixture
def client():
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def add_and_read(client, title="Solo Leveling", chapter=12):
    client.post("/ui/reading/series", data={"title": title, "kind": "manhwa", "source": "x", "start_chapter": "0",
                                            "genres": ["Action"]})
    sid = next(e["series_id"] for e in client.get("/reading/library").json() if e["title"] == title)
    client.post(f"/ui/reading/series/{sid}/progress", data={"chapter": str(chapter)})
    return sid


def test_page_shows_this_year_by_default(client):
    add_and_read(client)
    page = client.get("/ui/wrapped").text
    assert f"Your {THIS_YEAR}" in page or f">{THIS_YEAR}<" in page
    assert "Solo Leveling" in page and "Action" in page
    assert '<div class="stat-value">12' in page


def test_empty_year_and_trackers_are_shown_nicely(client):
    page = client.get("/ui/wrapped", params={"year": 2019}).text
    assert "Nothing read in 2019" in page and "Nothing finished in 2019" in page and "Nothing played in 2019" in page


def test_tracker_without_data_says_so_while_others_have_data(client):
    add_and_read(client)
    page = client.get("/ui/wrapped").text
    assert f"Nothing played in {THIS_YEAR}" in page and f"Nothing read in {THIS_YEAR}" not in page


def test_year_choice_lists_only_years_with_data(client):
    add_and_read(client)
    when = datetime(2023, 6, 1, 12, tzinfo=timezone.utc).isoformat()
    client.post("/listening/plays", json={"played_at": when, "track_id": "spotify:track:x", "track": "Old song",
                                          "artists": ["Someone"], "album": "A", "album_id": "a", "duration_ms": 200_000})
    page = client.get("/ui/wrapped").text
    assert f'href="#wrapped/{THIS_YEAR}"' in page and 'href="#wrapped/2023"' in page
    assert 'href="#wrapped/2024"' not in page
    assert "Old song" in client.get("/ui/wrapped", params={"year": 2023}).text


def test_bad_year_falls_back_to_this_year(client):
    assert client.get("/ui/wrapped", params={"year": "abc"}).status_code == 200


def test_compare_with_last_year(client):
    add_and_read(client)
    page = client.get("/ui/wrapped", params={"compare": 1}).text
    assert f"+12 vs {THIS_YEAR - 1}" in page
    assert "vs " + str(THIS_YEAR - 1) not in client.get("/ui/wrapped").text


def test_page_refreshes_after_changes_in_any_tracker(client):
    page = client.get("/ui/wrapped", params={"year": 2024}).text
    assert 'hx-get="/ui/wrapped?year=2024' in page
    assert all(f"{t}-changed from:body" in page for t in ("reading", "watching", "listening"))


def test_home_has_a_wrapped_teaser_but_it_is_no_tracker(client):
    add_and_read(client, chapter=7)
    home = client.get("/ui/home").text
    assert 'href="#wrapped"' in home and f"Wrapped {THIS_YEAR}" in home and "<b>7</b>" in home
    assert "Wrapped" not in client.get("/ui/settings").text
    assert 'href="#wrapped"' not in client.get("/").text  # geen link in de navigatiebalk
    assert "wrapped" in client.get("/static/trackly.js").text


def test_month_chart_has_twelve_months(client):
    add_and_read(client)
    page = client.get("/ui/wrapped").text
    assert page.count('class="wbar-month"') == 12
