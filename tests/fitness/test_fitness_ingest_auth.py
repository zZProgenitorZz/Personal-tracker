"""The ingest endpoints through an extra host (the iPhone): a Shortcut has no session cookie, so it sends
the bearer token PROGEN_INGEST_TOKEN. The token is good for /fitness/ingest/... and nothing else."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import app.clock as clock
from app.auth import EXTRA_HOSTS_ENV, INGEST_TOKEN_ENV, PASSWORD_HASH_ENV, SECRET_ENV, Auth, hash_password
from app.main import create_app

HOST = "my-pc.tail1234.ts.net"
TOKEN = "t" * 43
GYM = {"events": [{"event": "arrived", "at": "2026-10-16T10:00:00Z"}]}
STEPS = {"days": [{"day": "2026-10-16", "steps": 8123}]}


@pytest.fixture
def make_app(monkeypatch, tmp_path):
    monkeypatch.setattr(clock, "local_now", lambda: dt.datetime(2026, 10, 17, 12, 0))
    for key in (EXTRA_HOSTS_ENV, PASSWORD_HASH_ENV, SECRET_ENV, INGEST_TOKEN_ENV):
        monkeypatch.delenv(key, raising=False)

    def make(token: str | None = TOKEN):
        monkeypatch.setenv(EXTRA_HOSTS_ENV, HOST)
        monkeypatch.setenv(PASSWORD_HASH_ENV, hash_password("correct horse battery"))
        monkeypatch.setenv(SECRET_ENV, "s" * 43)
        if token:
            monkeypatch.setenv(INGEST_TOKEN_ENV, token)
        return create_app(":memory:", env_file=tmp_path / "no.env")
    return make


def phone(app) -> TestClient:
    return TestClient(app, base_url=f"https://{HOST}", follow_redirects=False)


def bearer(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_the_right_token_lets_a_shortcut_in_without_a_session(make_app):
    client = phone(make_app())
    assert client.post("/fitness/ingest/steps", json=STEPS, headers=bearer()).json() == {"recorded": 1, "unchanged": 0}
    assert client.post("/fitness/ingest/gym", json=GYM, headers=bearer()).json() == {"recorded": 1, "unchanged": 0}


def test_the_word_bearer_may_be_in_any_case(make_app):
    response = phone(make_app()).post("/fitness/ingest/steps", json=STEPS, headers={"Authorization": f"bearer {TOKEN}"})
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [{}, bearer("wrong"), bearer(TOKEN + "x"), bearer(TOKEN[:-1]), bearer(""),
                                     {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}])
def test_no_or_a_wrong_token_is_a_401_and_saves_nothing(make_app, headers):
    app = make_app()
    client = phone(app)
    response = client.post("/fitness/ingest/steps", json=STEPS, headers=headers)
    assert response.status_code == 401 and "token" in response.json()["detail"]
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.post("/fitness/ingest/gym", json=GYM, headers=headers).status_code == 401
    assert pc_visits(app) == [] and pc_steps(app) == []


def test_a_non_ascii_token_is_just_wrong_not_an_error(make_app):
    wrong = {"Authorization": "Bearer tést".encode("latin-1")}  # a header is bytes on the wire
    response = phone(make_app()).post("/fitness/ingest/steps", json=STEPS, headers=wrong)
    assert response.status_code == 401


def test_without_a_token_in_env_the_answer_says_what_to_do(make_app):
    response = phone(make_app(token=None)).post("/fitness/ingest/steps", json=STEPS, headers=bearer())
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert INGEST_TOKEN_ENV in detail and ".env" in detail and "restart" in detail.lower()
    assert "secrets.token_urlsafe(32)" in detail


def test_the_token_opens_nothing_else(make_app):
    client = phone(make_app())
    assert client.get("/fitness/steps", headers=bearer()).status_code == 401
    assert client.get("/reading/library", headers=bearer()).status_code == 401
    assert client.post("/ui/assistant/reset", headers={**bearer(), "HX-Request": "true"}).status_code == 401
    assert client.post("/fitness/other", json={}, headers=bearer()).status_code == 401
    assert client.get("/ui/home", headers={**bearer(), "HX-Request": "true"}).status_code == 401


def test_a_signed_in_session_needs_no_token(make_app):
    client = phone(make_app())
    client.post("/login", data={"password": "correct horse battery"}, headers={"HX-Request": "true"})
    assert client.post("/fitness/ingest/steps", json=STEPS).status_code == 200


def test_on_the_pc_itself_no_token_is_needed(make_app):
    client = TestClient(make_app(), base_url="http://127.0.0.1:8000", follow_redirects=False)
    assert client.post("/fitness/ingest/steps", json=STEPS).status_code == 200


def test_through_a_proxy_on_the_pc_the_token_is_needed(make_app):
    # tailscale serve forwards to 127.0.0.1 with forwarding headers
    client = TestClient(make_app(), base_url="http://127.0.0.1:8000", follow_redirects=False)
    forwarded = {"X-Forwarded-For": "100.64.0.7"}
    assert client.post("/fitness/ingest/steps", json=STEPS, headers=forwarded).status_code == 401
    assert client.post("/fitness/ingest/steps", json=STEPS, headers={**forwarded, **bearer()}).status_code == 200


def test_the_csrf_rules_are_untouched(make_app):
    client = phone(make_app())
    plain = client.post("/fitness/ingest/gym", content="{}", headers={**bearer(), "Content-Type": "text/plain"})
    assert plain.status_code == 403
    cross_site = client.post("/fitness/ingest/gym", json=GYM, headers={**bearer(), "Origin": "https://evil.example.com"})
    assert cross_site.status_code == 403


def test_the_token_comes_from_the_environment_and_is_trimmed():
    auth = Auth.from_environment({EXTRA_HOSTS_ENV: HOST, PASSWORD_HASH_ENV: "x", SECRET_ENV: "y",
                                  INGEST_TOKEN_ENV: f"  {TOKEN}\n"})
    assert auth.ingest_token == TOKEN
    assert Auth.from_environment({}).ingest_token == ""


def pc_steps(app) -> list:
    return TestClient(app, base_url="http://127.0.0.1:8000").get("/fitness/steps").json()


def pc_visits(app) -> list:
    return TestClient(app, base_url="http://127.0.0.1:8000").get("/fitness/visits").json()
