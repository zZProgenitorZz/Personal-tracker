"""Progen on your phone through Tailscale: extra host names, signing in there (and only there),
the session cookie, rate limiting and the iPhone bits. Never the real .env."""
import logging

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import auth as auth_module
from app.auth import (
    COOKIE, EXTRA_HOSTS_ENV, MAX_FAILURES, PASSWORD_HASH_ENV, SECRET_ENV, SESSION_SECONDS, Auth, AuthConfigError,
    hash_password, is_https, make_session, session_valid, set_password, verify_password,
)
from app.auth_web import safe_next
from app.main import create_app

HOST = "my-pc.tail1234.ts.net"
PASSWORD = "correct horse battery"
HTMX = {"HX-Request": "true"}
PAGE = {"Accept": "text/html"}


@pytest.fixture
def make_app(monkeypatch, tmp_path):
    """create_app with only the phone settings we give it (and no .env file at all)."""
    for key in (EXTRA_HOSTS_ENV, PASSWORD_HASH_ENV, SECRET_ENV):
        monkeypatch.delenv(key, raising=False)

    def make(hosts: str = HOST, password: str | None = PASSWORD):
        if hosts:
            monkeypatch.setenv(EXTRA_HOSTS_ENV, hosts)
        if password:
            monkeypatch.setenv(PASSWORD_HASH_ENV, hash_password(password))
            monkeypatch.setenv(SECRET_ENV, "s" * 43)
        return create_app(":memory:", env_file=tmp_path / "no.env")
    return make


def phone(app, **kwargs) -> TestClient:
    return TestClient(app, base_url=f"https://{HOST}", follow_redirects=False, **kwargs)


def pc(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)


def sign_in(client: TestClient, password: str = PASSWORD, next_url: str = "/"):
    return client.post("/login", data={"password": password, "next": next_url}, headers=HTMX)


# ---- Host names ----

def test_an_extra_host_is_allowed_and_other_names_are_not(make_app):
    app = make_app()
    assert phone(app).get("/login").status_code == 200
    stranger = TestClient(app, base_url="https://evil.example.com", follow_redirects=False)
    assert stranger.get("/login").status_code == 400  # DNS rebinding guard still on


def test_without_extra_hosts_nothing_changes(make_app):
    app = make_app(hosts="", password=None)
    assert phone(app).get("/").status_code == 400  # the Tailscale name isn't allowed
    assert pc(app).get("/ui/home", headers=HTMX).status_code == 200  # and no login on the PC


def test_an_origin_from_the_extra_host_is_progen_itself(make_app):
    app = make_app()
    client = phone(app)
    sign_in(client)
    own = client.post("/ui/assistant/reset", headers={**HTMX, "Origin": f"https://{HOST}"})
    assert own.status_code == 200
    other = client.post("/ui/assistant/reset", headers={**HTMX, "Origin": "https://evil.example.com"})
    assert other.status_code == 403


def test_extra_hosts_without_a_password_refuse_to_start(make_app):
    with pytest.raises(AuthConfigError, match="python -m app.auth set-password"):
        make_app(password=None)


@pytest.mark.parametrize("value", ["*.ts.net", "my-pc.tail1234.ts.net:443", "https://my-pc.tail1234.ts.net"])
def test_wildcards_and_urls_are_refused(make_app, value):
    with pytest.raises(AuthConfigError):
        make_app(hosts=value)


def test_the_extra_hosts_are_logged_at_startup(make_app, caplog):
    with caplog.at_level(logging.WARNING, logger="progen"):
        make_app(hosts=f"{HOST}, Second.ts.net")
    assert f"Also reachable as {HOST}, second.ts.net" in caplog.text


# ---- Signing in: through an extra host, not on the PC ----

def test_through_the_extra_host_you_sign_in_first(make_app):
    client = phone(make_app())
    page = client.get("/", headers=PAGE)
    assert page.status_code == 303 and page.headers["location"] == "/login?next=%2F"
    fragment = client.get("/ui/home", headers=HTMX)
    assert fragment.status_code == 401 and fragment.headers["HX-Redirect"] == "/login?next=%2Fui%2Fhome"
    assert client.get("/reading/library").status_code == 401  # the JSON API and scripts
    assert client.post("/ui/assistant/reset", headers=HTMX).status_code == 401


def test_the_login_page_static_files_and_the_spotify_callback_need_no_session(make_app):
    client = phone(make_app())
    assert "Sign in" in client.get("/login").text
    assert client.get("/static/trackly.css").status_code == 200
    assert client.get("/static/icons/icon-180.png").status_code == 200
    callback = client.get("/listening/spotify/callback?error=access_denied", headers=PAGE)
    assert callback.status_code != 401 and "/login" not in callback.headers.get("location", "")


def test_on_the_pc_itself_no_login(make_app):
    client = pc(make_app())
    assert client.get("/ui/home", headers=HTMX).status_code == 200
    assert client.get("/", headers=PAGE).status_code == 200


def test_a_request_through_a_proxy_needs_login_even_as_localhost(make_app):
    # tailscale serve forwards to 127.0.0.1; a forged Host: 127.0.0.1 through it must not skip the login.
    client = pc(make_app())
    proxied = client.get("/ui/home", headers={**HTMX, "X-Forwarded-For": "100.64.0.7"})
    assert proxied.status_code == 401


def test_the_right_password_sets_the_cookie_and_sends_you_on(make_app):
    client = phone(make_app())
    wrong = sign_in(client, "nope")
    assert "not the password" in wrong.text and "set-cookie" not in wrong.headers
    right = sign_in(client, next_url="/#reading")
    assert right.headers["HX-Redirect"] == "/#reading"
    cookie = right.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert f"Max-Age={SESSION_SECONDS}" in cookie and SESSION_SECONDS == 30 * 24 * 60 * 60
    assert "Secure" in cookie  # it came in over https
    assert PASSWORD not in cookie
    assert client.get("/ui/home", headers=HTMX).status_code == 200


def test_the_login_never_sends_you_to_another_site():
    assert [safe_next(t) for t in ["/#planner", "//evil.com", "/\\evil.com", "https://evil.com", ""]] == [
        "/#planner", "/", "/", "/", "/"]


def test_an_expired_session_gets_hx_redirect_to_the_login(make_app):
    app = make_app()
    client = phone(app)
    sign_in(client)
    now = [app.state.auth.clock()]
    app.state.auth.clock = lambda: now[0]
    now[0] += SESSION_SECONDS + 1
    expired = client.get("/ui/home", headers=HTMX)
    assert expired.status_code == 401 and expired.headers["HX-Redirect"].startswith("/login")


def test_wrong_passwords_are_rate_limited(make_app):
    app = make_app()
    now = [1_000_000.0]
    app.state.auth.clock = lambda: now[0]
    client = phone(app)
    for _ in range(MAX_FAILURES):
        assert "not the password" in sign_in(client, "nope").text
    blocked = sign_in(client)  # even the right one, for now
    assert "Too many wrong tries" in blocked.text and "HX-Redirect" not in blocked.headers
    now[0] += 15 * 60 + 1
    assert sign_in(client).headers["HX-Redirect"] == "/"


def test_logout_is_in_settings_and_ends_the_session(make_app):
    app = make_app()
    client = phone(app)
    sign_in(client)
    settings = client.get("/ui/settings", headers=HTMX).text
    assert "Phone access" in settings and HOST in settings and 'hx-post="/logout"' in settings
    assert 'hx-post="/logout"' not in pc(app).get("/ui/settings", headers=HTMX).text  # nothing to log out of there
    out = client.post("/logout", headers=HTMX)
    assert out.headers["HX-Redirect"] == "/login" and "Max-Age=0" in out.headers["set-cookie"]
    assert client.get("/ui/home", headers=HTMX).status_code == 401


# ---- https and X-Forwarded-Proto ----

def scope(scheme="http", client="127.0.0.1"):
    return {"scheme": scheme, "client": (client, 50000)}


def test_x_forwarded_proto_is_only_believed_from_this_computer():
    https = {"x-forwarded-proto": "https"}
    assert is_https(scope("https", "100.64.0.7"), {})
    assert is_https(scope(client="127.0.0.1"), https)
    assert not is_https(scope(client="100.64.0.7"), https)
    assert not is_https(scope(client="127.0.0.1"), {})


def test_the_cookie_is_secure_through_tailscale_serve_but_not_from_elsewhere(make_app):
    app = make_app()
    proxy = TestClient(app, base_url=f"http://{HOST}", follow_redirects=False, client=("127.0.0.1", 50000))
    forwarded = {**HTMX, "X-Forwarded-Proto": "https"}
    assert "Secure" in proxy.post("/login", data={"password": PASSWORD}, headers=forwarded).headers["set-cookie"]
    other = TestClient(app, base_url=f"http://{HOST}", follow_redirects=False, client=("100.64.0.7", 50000))
    assert "Secure" not in other.post("/login", data={"password": PASSWORD}, headers=forwarded).headers["set-cookie"]


# ---- Password, session and the script ----

def test_the_password_is_only_kept_as_a_scrypt_hash():
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt:") and PASSWORD not in stored and "$" not in stored
    assert verify_password(PASSWORD, stored) and not verify_password("wrong", stored)
    assert not verify_password(PASSWORD, "garbage")


def test_a_new_password_ends_old_sessions():
    token = make_session("secret", "hash-1", now=0)
    assert session_valid(token, "secret", "hash-1", now=10)
    assert not session_valid(token, "secret", "hash-2", now=10)
    assert not session_valid(token, "other", "hash-1", now=10)
    assert not session_valid(token + "0", "secret", "hash-1", now=10)
    assert not session_valid(token, "secret", "hash-1", now=SESSION_SECONDS + 1)


def test_set_password_writes_only_the_hash_and_a_secret(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=keep-me\nPROGEN_PASSWORD_HASH=old\n", encoding="utf-8")
    answers, said = iter([PASSWORD, PASSWORD]), []
    assert set_password(env, ask=lambda prompt: next(answers), out=said.append) == 0
    text = env.read_text(encoding="utf-8")
    assert PASSWORD not in text and "ANTHROPIC_API_KEY=keep-me" in text and "=old" not in text
    values = dict(line.split("=", 1) for line in text.splitlines())
    assert verify_password(PASSWORD, values[PASSWORD_HASH_ENV]) and len(values[SECRET_ENV]) >= 40
    auth = Auth.from_environment({EXTRA_HOSTS_ENV: HOST, **values})
    assert auth.check_password(PASSWORD)


def test_set_password_changes_nothing_when_the_two_differ(tmp_path):
    env = tmp_path / ".env"
    answers = iter([PASSWORD, "something else"])
    assert set_password(env, ask=lambda prompt: next(answers), out=lambda line: None) == 1
    assert not env.exists()


def test_the_script_has_one_command(capsys):
    assert auth_module.main(["nonsense"]) == 2 and "set-password" in capsys.readouterr().out


# ---- On the iPhone ----

def test_the_page_is_ready_for_the_home_screen(make_app):
    shell = pc(make_app(hosts="", password=None)).get("/").text
    assert "viewport-fit=cover" in shell and '"apple-mobile-web-app-capable" content="yes"' in shell
    assert '"apple-mobile-web-app-status-bar-style" content="black-translucent"' in shell
    assert '"apple-mobile-web-app-title" content="Progen"' in shell
    assert 'rel="apple-touch-icon" href="/static/icons/icon-180.png" sizes="180x180"' in shell
    with Image.open("app/static/icons/icon-180.png") as icon:
        assert icon.size == (180, 180) and icon.mode == "RGB"  # no transparency: iOS would turn it black


def test_chapters_are_quick_to_log_on_a_phone(make_app):
    app = make_app(hosts="", password=None)
    client = pc(app)
    client.post("/ui/reading/series", data={"title": "Solo Leveling", "kind": "manhwa", "source": "x",
                                            "start_chapter": "119"}, headers=HTMX)
    library = client.get("/ui/reading/library", headers=HTMX).text
    assert 'class="btn-plus"' in library and 'hx-vals=\'{"chapter": 120}\'' in library  # one tap, no dialog
    assert 'inputmode="decimal"' in library  # chapters like 45.5 need the point
    css = client.get("/static/trackly.css").text
    assert "min-height: 44px" in css and "env(safe-area-inset-bottom)" in css
