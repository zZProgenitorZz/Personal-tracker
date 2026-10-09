"""Gathering dust: series op Reading die al een tijd niet zijn bijgewerkt. Alleen lezen."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.reading.web as reading_web
from app.main import create_app
from app.reading.events import Kind, ProgressLogged, SeriesStarted, Status, StatusChanged
from app.reading.projections import LibraryProjection

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def days_ago(n, hours=0):
    return NOW - timedelta(days=n, hours=hours)


def given(*events) -> LibraryProjection:
    library = LibraryProjection()
    for event in events:
        library.apply(event)
    return library


def start(sid, title, when):
    return SeriesStarted(sid, title, Kind.MANHWA, "x", 1, at=when)


def test_reading_series_untouched_for_longer_than_the_limit_are_stale():
    library = given(start("1", "Fresh", days_ago(40)), ProgressLogged("1", 5, 1, at=days_ago(3)),
                    start("2", "Dusty", days_ago(40)), ProgressLogged("2", 5, 1, at=days_ago(30)),
                    start("3", "Never touched", days_ago(25)))
    assert [e.title for e in library.stale(NOW, 21)] == ["Dusty", "Never touched"]  # langst stil eerst


def test_exactly_the_limit_is_not_stale_yet():
    library = given(start("1", "A", days_ago(21)), start("2", "B", days_ago(21, hours=1)))
    assert [e.title for e in library.stale(NOW, 21)] == ["B"]


@pytest.mark.parametrize("status", [Status.PLAN_TO_READ, Status.ON_HOLD, Status.COMPLETED, Status.DROPPED])
def test_other_statuses_are_never_stale(status):
    library = given(start("1", "A", days_ago(100)), StatusChanged("1", Status.READING, status, at=days_ago(90)))
    assert library.stale(NOW, 21) == []


def test_a_status_change_back_to_reading_counts_as_activity():
    library = given(start("1", "A", days_ago(100)),
                    StatusChanged("1", Status.READING, Status.ON_HOLD, at=days_ago(90)),
                    StatusChanged("1", Status.ON_HOLD, Status.READING, at=days_ago(2)))
    assert library.stale(NOW, 21) == []


# ---- Dashboard en startscherm ----

@pytest.fixture
def client():
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def add(client, title, status="reading"):
    client.post("/ui/reading/series", data={"title": title, "kind": "manhwa", "source": "x", "start_chapter": "4",
                                            "status": status})
    return next(e["series_id"] for e in client.get("/reading/library").json() if e["title"] == title)


def later(monkeypatch, days):
    """De klok van de webpagina vooruitzetten; de events blijven op hun echte tijd."""
    real = reading_web.utc_now
    monkeypatch.setattr(reading_web, "utc_now", lambda: real() + timedelta(days=days))


def test_no_panel_when_nothing_is_stale(client):
    add(client, "Solo Leveling")
    assert "Gathering dust" not in client.get("/ui/reading/dashboard").text
    assert "gathering dust" not in client.get("/ui/home").text


def test_panel_with_actions(client, monkeypatch):
    sid = add(client, "Solo Leveling")
    add(client, "Backlog", "plan_to_read")
    later(monkeypatch, reading_web.STALE_READING_DAYS + 7)
    page = client.get("/ui/reading/dashboard").text
    panel = page.split("Gathering dust", 1)[1].split("</section>", 1)[0]
    assert "Solo Leveling" in panel and "Backlog" not in panel
    assert "Chapter 4" in panel and "4 weeks ago" in panel
    base = f"/ui/reading/series/{sid}"
    assert f'hx-post="{base}/progress"' in panel and '{"chapter": 5}' in panel        # Log chapter
    assert f'hx-post="{base}/status"' in panel and '"on_hold"' in panel and '"dropped"' in panel
    assert 'data-confirm-kind="drop"' in panel                                          # Drop vraagt eerst
    assert "data-dust-dismiss" in panel                                                 # Not now


def test_actions_use_the_existing_status_endpoint(client, monkeypatch):
    sid = add(client, "Solo Leveling")
    later(monkeypatch, 30)
    response = client.post(f"/ui/reading/series/{sid}/status", data={"status": "on_hold"})
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert "Gathering dust" not in client.get("/ui/reading/dashboard").text


def test_at_most_five_then_more(client, monkeypatch):
    for i in range(7):
        add(client, f"Series {i}")
    later(monkeypatch, 30)
    page = client.get("/ui/reading/dashboard").text
    assert "+2 more" in page and page.count('class="dust-row"') == 7


def test_home_card_mentions_dust(client, monkeypatch):
    add(client, "A")
    add(client, "B")
    later(monkeypatch, 30)
    assert "2 gathering dust" in client.get("/ui/home").text
