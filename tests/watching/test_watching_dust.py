"""Gathering dust bij Watching: titels op Watching zonder statuswijziging sinds een tijd. Alleen lezen."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.watching.web as watching_web
from app.main import create_app
from app.watching.events import ShowAdded, ShowGenresChanged, ShowStatusChanged, WatchKind, WatchStatus
from app.watching.projections import WatchlistProjection

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def days_ago(n):
    return NOW - timedelta(days=n)


def given(*events) -> WatchlistProjection:
    watchlist = WatchlistProjection()
    for event in events:
        watchlist.apply(event)
    return watchlist


def test_watching_titles_untouched_for_longer_than_the_limit_are_stale():
    watchlist = given(
        ShowAdded("1", "Old", WatchKind.SERIES, WatchStatus.WATCHING, at=days_ago(60)),
        ShowAdded("2", "Older", WatchKind.ANIME, WatchStatus.WATCHING, at=days_ago(90)),
        ShowAdded("3", "New", WatchKind.ANIME, WatchStatus.WATCHING, at=days_ago(10)),
        ShowAdded("4", "Resumed", WatchKind.SERIES, WatchStatus.ON_HOLD, at=days_ago(90)),
        ShowStatusChanged("4", WatchStatus.ON_HOLD, WatchStatus.WATCHING, at=days_ago(5)),
        ShowGenresChanged("1", ["Drama"], at=days_ago(1)),  # genres aanpassen is geen kijken
    )
    assert [e.title for e in watchlist.stale(NOW, 30)] == ["Older", "Old"]


@pytest.mark.parametrize("status", [s for s in WatchStatus if s is not WatchStatus.WATCHING])
def test_other_statuses_are_never_stale(status):
    watchlist = given(ShowAdded("1", "A", WatchKind.MOVIE, status, at=days_ago(400)))
    assert watchlist.stale(NOW, 30) == []


@pytest.fixture
def client():
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def add(client, title, status="watching"):
    client.post("/ui/watching/shows", data={"title": title, "kind": "series", "status": status})
    return next(e["show_id"] for e in client.get("/watching/list").json() if e["title"] == title)


def later(monkeypatch, days):
    real = watching_web.utc_now
    monkeypatch.setattr(watching_web, "utc_now", lambda: real() + timedelta(days=days))


def test_panel_only_after_the_limit(client, monkeypatch):
    sid = add(client, "Severance")
    add(client, "Dune", "plan_to_watch")
    later(monkeypatch, watching_web.STALE_WATCHING_DAYS - 1)
    assert "Gathering dust" not in client.get("/ui/watching/dashboard").text
    later(monkeypatch, 2)
    panel = client.get("/ui/watching/dashboard").text.split("Gathering dust", 1)[1].split("</section>", 1)[0]
    assert "Severance" in panel and "Dune" not in panel
    assert f'hx-post="/ui/watching/shows/{sid}/status"' in panel and 'data-confirm-kind="drop"' in panel
    assert "data-dust-dismiss" in panel and "/progress" not in panel


def test_home_card_mentions_dust(client, monkeypatch):
    add(client, "Severance")
    later(monkeypatch, 45)
    assert "1 gathering dust" in client.get("/ui/home").text
