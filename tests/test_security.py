"""Bescherming tegen andere websites (CSRF) en DNS rebinding.

Progen draait op je eigen computer, maar je browser kan ook verzoeken sturen
namens een website die je open hebt. Die mogen nooit iets veranderen."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

FORM = {"title": "Solo Leveling", "kind": "manhwa", "source": "x", "start_chapter": "1"}


@pytest.fixture
def app():
    return create_app(":memory:")


def browser(app, **headers):
    """Een browser zonder de header van de app, zoals een andere website hem stuurt."""
    return TestClient(app, headers=headers)


def library(app):
    return TestClient(app).get("/reading/library").json()


# ---- Wijzigingen zonder bewijs dat ze van Progen komen ----

@pytest.mark.parametrize("method, path, data", [
    ("post", "/ui/reading/series", FORM),
    ("post", "/ui/backups", {}),
    ("post", "/ui/watching/shows", {"title": "Dune", "kind": "movie"}),
    ("post", "/ui/listening/spotify/disconnect", {}),
    ("post", "/ui/autostart", {"enabled": "on"}),
])
def test_form_post_from_another_site_is_refused(app, method, path, data):
    response = getattr(browser(app), method)(path, data=data)
    assert response.status_code == 403


def test_text_plain_post_pretending_to_be_json_is_refused(app):
    # Een formulier op een andere site kan enctype=text/plain sturen met JSON-achtige inhoud.
    body = '{"title": "Hacked", "kind": "manhwa", "source": "x"}'
    response = browser(app).post("/reading/series", content=body, headers={"Content-Type": "text/plain"})
    assert response.status_code == 403 and library(app) == []


def test_delete_without_header_is_refused(app):
    TestClient(app).post("/reading/series", json={"title": "Solo Leveling", "kind": "manhwa", "source": "x"})
    sid = library(app)[0]["series_id"]
    assert browser(app).delete(f"/ui/reading/series/{sid}").status_code == 403
    assert len(library(app)) == 1


def test_reading_is_always_allowed(app):
    assert browser(app).get("/ui/reading/dashboard").status_code == 200
    assert browser(app).get("/reading/library").status_code == 200


# ---- Wat wél mag ----

def test_the_app_itself_may_change_things(app):
    response = browser(app, **{"HX-Request": "true"}).post("/ui/reading/series", data=FORM)
    assert response.status_code == 200 and len(library(app)) == 1


def test_json_api_may_change_things(app):
    response = browser(app).post("/reading/series", json={"title": "Solo Leveling", "kind": "manhwa", "source": "x"})
    assert response.status_code == 201


# ---- Andere websites, ook als ze de header proberen ----

def test_request_from_another_origin_is_refused_even_with_header(app):
    client = browser(app, **{"HX-Request": "true", "Origin": "https://evil.example"})
    assert client.post("/ui/reading/series", data=FORM).status_code == 403
    assert library(app) == []


def test_browser_saying_cross_site_is_refused(app):
    client = browser(app, **{"HX-Request": "true", "Sec-Fetch-Site": "cross-site"})
    assert client.post("/ui/reading/series", data=FORM).status_code == 403


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8000", "http://localhost:8000"])
def test_own_origin_is_fine(origin):
    app = create_app(":memory:")
    client = TestClient(app, base_url=origin, headers={"HX-Request": "true", "Origin": origin,
                                                       "Sec-Fetch-Site": "same-origin"})
    assert client.post("/ui/reading/series", data=FORM).status_code == 200


# ---- DNS rebinding: een vreemde naam die naar 127.0.0.1 wijst ----

def test_unknown_host_name_is_refused(app):
    client = TestClient(app, base_url="http://evil.example:8000")
    assert client.get("/reading/library").status_code == 400
    assert client.get("/").status_code == 400


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8000", "http://localhost:8000"])
def test_own_host_names_are_fine(base_url):
    assert TestClient(create_app(":memory:"), base_url=base_url).get("/").status_code == 200
