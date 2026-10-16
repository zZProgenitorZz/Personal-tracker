"""Signing in, only for requests through an extra host name (PROGEN_EXTRA_HOSTS, e.g. your phone
through Tailscale). On this computer itself (127.0.0.1 / localhost) Progen stays one click.

- The password is stored only as a scrypt hash (hashlib, standard library) in .env, together with
  a random session secret. Set both with:  python -m app.auth set-password
- A session is a signed cookie (HMAC-SHA256 of its expiry and the password hash, so a new password
  ends every session): HttpOnly, SameSite=Lax, Secure over https, valid for 30 days.
- Wrong passwords are rate limited per client.
- Extra hosts without a password: create_app refuses to start (AuthConfigError).
- The one exception to the session: the ingest paths (/fitness/ingest/...) also accept the bearer token
  PROGEN_INGEST_TOKEN, because an iPhone Shortcut has no session cookie. Nothing else does.
"""
import base64
import getpass
import hashlib
import hmac
import secrets
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "progen_session"
SESSION_DAYS = 30
SESSION_SECONDS = SESSION_DAYS * 24 * 60 * 60
EXTRA_HOSTS_ENV = "PROGEN_EXTRA_HOSTS"
PASSWORD_HASH_ENV = "PROGEN_PASSWORD_HASH"
SECRET_ENV = "PROGEN_SESSION_SECRET"
INGEST_TOKEN_ENV = "PROGEN_INGEST_TOKEN"
INGEST_PREFIX = "/fitness/ingest/"  # the only paths a bearer token is good for
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}  # about 16 MB and a few dozen ms per check
MAX_FAILURES = 5                 # wrong passwords per client ...
FAILURE_WINDOW = 15 * 60         # ... within this many seconds, then wait until the window has passed
LOOPBACK = {"127.0.0.1", "::1"}
# No login needed: the login page itself, styles and icons (the login page and the home screen icon
# use them), and Spotify sending you back after connecting (that happens on the PC).
EXEMPT_PATHS = ("/login", "/static/", "/listening/spotify/callback")
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded")


class AuthConfigError(RuntimeError):
    """Settings in .env that don't fit together. The message says what to do."""


# ---- Password and session ----

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    """'scrypt:n:r:p:salt:hash' (no $, so .env never tries to expand it)."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt:{SCRYPT['n']}:{SCRYPT['r']}:{SCRYPT['p']}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split(":")
        if kind != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _signature(secret: str, password_hash: str, expires: int) -> str:
    message = f"progen-session:{expires}:{password_hash}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def make_session(secret: str, password_hash: str, now: float) -> str:
    expires = int(now) + SESSION_SECONDS
    return f"{expires}.{_signature(secret, password_hash, expires)}"


def session_valid(token: str | None, secret: str, password_hash: str, now: float) -> bool:
    try:
        expires_text, signature = (token or "").split(".", 1)
        expires = int(expires_text)
    except ValueError:
        return False
    return expires > now and hmac.compare_digest(signature, _signature(secret, password_hash, expires))


# ---- Settings ----

def parse_extra_hosts(value: str | None) -> list[str]:
    """'my-pc.tail1234.ts.net, other.ts.net' -> host names, lower case. No wildcards."""
    hosts = [h.strip().lower() for h in (value or "").split(",") if h.strip()]
    for host in hosts:
        if "*" in host:
            raise AuthConfigError(f"{EXTRA_HOSTS_ENV} may not contain wildcards ('{host}'). Name each host.")
        if "/" in host or ":" in host or " " in host:
            raise AuthConfigError(f"{EXTRA_HOSTS_ENV} wants host names only, like my-pc.tail1234.ts.net "
                                  f"(not '{host}').")
    return hosts


@dataclass
class Auth:
    extra_hosts: list[str]
    password_hash: str = ""
    secret: str = ""
    ingest_token: str = ""
    clock: object = time.time
    _failures: dict = field(default_factory=lambda: defaultdict(deque))

    @classmethod
    def from_environment(cls, environ) -> "Auth":
        auth = cls(parse_extra_hosts(environ.get(EXTRA_HOSTS_ENV)),
                   (environ.get(PASSWORD_HASH_ENV) or "").strip(), (environ.get(SECRET_ENV) or "").strip(),
                   (environ.get(INGEST_TOKEN_ENV) or "").strip())
        if auth.extra_hosts and not (auth.password_hash and auth.secret):
            raise AuthConfigError(
                f"{EXTRA_HOSTS_ENV} is set ({', '.join(auth.extra_hosts)}), but there is no password, so anyone "
                f"who can reach those hosts could use Progen. Set one with:  python -m app.auth set-password  "
                f"(or empty {EXTRA_HOSTS_ENV} in .env).")
        return auth

    @property
    def enabled(self) -> bool:
        return bool(self.extra_hosts)

    def check_password(self, password: str) -> bool:
        return verify_password(password, self.password_hash)

    def new_session(self) -> str:
        return make_session(self.secret, self.password_hash, self.clock())

    def valid(self, token: str | None) -> bool:
        return session_valid(token, self.secret, self.password_hash, self.clock())

    def ingest_refusal(self, headers: dict[str, str]) -> JSONResponse | None:
        """None if the request carries the ingest token; otherwise the response to refuse it with."""
        if not self.ingest_token:
            return JSONResponse({"detail": f"Ingest isn't set up. Put {INGEST_TOKEN_ENV}=<a long random token> in "
                                           f".env (python -c \"import secrets; print(secrets.token_urlsafe(32))\") "
                                           f"and restart Progen."}, status_code=503)
        scheme, _, supplied = headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(supplied.strip().encode("utf-8"),
                                                              self.ingest_token.encode("utf-8")):
            return None
        return JSONResponse({"detail": "Missing or wrong ingest token"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})

    # ---- Rate limiting ----

    def wait_seconds(self, client: str) -> int:
        """0 if this client may try a password; otherwise how long until it may again."""
        recent = self._recent(client)
        if len(recent) < MAX_FAILURES:
            return 0
        return max(1, int(recent[0] + FAILURE_WINDOW - self.clock()))

    def failed(self, client: str) -> None:
        self._recent(client).append(self.clock())

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)

    def _recent(self, client: str) -> deque:
        recent = self._failures[client]
        while recent and recent[0] <= self.clock() - FAILURE_WINDOW:
            recent.popleft()
        return recent


# ---- Requests ----

def headers_of(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}


def host_of(headers: dict[str, str]) -> str:
    """The Host header without its port: 'my-pc.tail1234.ts.net:443' -> 'my-pc.tail1234.ts.net'."""
    host = headers.get("host", "").lower()
    if host.startswith("["):  # [::1]:8000
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if ":" in host else host


def is_https(scope: Scope, headers: dict[str, str]) -> bool:
    """https directly, or through a proxy on this computer (tailscale serve) that says so.
    X-Forwarded-Proto is only believed from 127.0.0.1: anyone else could send it."""
    if scope.get("scheme") == "https":
        return True
    client = (scope.get("client") or ("",))[0]
    return client in LOOPBACK and headers.get("x-forwarded-proto", "").strip().lower() == "https"


def needs_login(auth: Auth, headers: dict[str, str]) -> bool:
    """Through an extra host, always. 127.0.0.1/localhost only when it came through a proxy: a
    request on this computer itself has no forwarding headers, one through tailscale serve does."""
    if not auth.enabled:
        return False
    return host_of(headers) in auth.extra_hosts or any(h in headers for h in PROXY_HEADERS)


def session_cookie(token: str, secure: bool, max_age: int = SESSION_SECONDS) -> str:
    return (f"{COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Lax"
            + ("; Secure" if secure else ""))


def cookie_of(headers: dict[str, str]) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


class LoginMiddleware:
    """Without a valid session, a request that needs login gets the login page (a page), HX-Redirect
    (htmx) or a 401 (the JSON API and scripts)."""

    def __init__(self, app: ASGIApp, auth: Auth):
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = headers_of(scope)
        signed_in = self.auth.enabled and self.auth.valid(cookie_of(headers))
        scope.setdefault("state", {})["signed_in"] = signed_in
        path = scope["path"]
        if signed_in or not needs_login(self.auth, headers) or path.startswith(EXEMPT_PATHS):
            await self.app(scope, receive, send)
            return
        if path.startswith(INGEST_PREFIX):  # a Shortcut: the token instead of a session
            refusal = self.auth.ingest_refusal(headers)
            if refusal is None:
                await self.app(scope, receive, send)
            else:
                await refusal(scope, receive, send)
            return
        target = path + (f"?{scope['query_string'].decode('latin-1')}" if scope.get("query_string") else "")
        login = f"/login?next={quote(target, safe='')}"
        if headers.get("hx-request") == "true":
            response = PlainTextResponse("Sign in again.", status_code=401, headers={"HX-Redirect": login})
        elif scope["method"] == "GET" and "text/html" in headers.get("accept", ""):
            response = RedirectResponse(login, status_code=303)
        else:
            response = JSONResponse({"detail": "Sign in first"}, status_code=401)
        await response(scope, receive, send)


# ---- python -m app.auth set-password ----

def write_env(path: Path, values: dict[str, str]) -> None:
    """Sets KEY=value lines in .env: replaces existing ones, appends the rest, keeps everything else."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={values[key]}"
            done.add(key)
    lines += [f"{key}={value}" for key, value in values.items() if key not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_password(env_file: Path, ask=getpass.getpass, out=print) -> int:
    password = ask("New password for Progen: ")
    if len(password) < 8:
        out("Use at least 8 characters.")
        return 1
    if ask("Again: ") != password:
        out("The two passwords differ. Nothing changed.")
        return 1
    # Only the hash and a new secret go into .env, never the password; a new secret ends old sessions.
    write_env(env_file, {PASSWORD_HASH_ENV: hash_password(password), SECRET_ENV: secrets.token_urlsafe(32)})
    out(f"Password set in {env_file}. Restart Progen to use it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["set-password"]:
        print("Usage: python -m app.auth set-password")
        return 2
    return set_password(Path(__file__).resolve().parent.parent / ".env")


if __name__ == "__main__":
    sys.exit(main())
