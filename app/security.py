"""Protection against other websites: CSRF and DNS rebinding.

Progen only listens on your own computer (127.0.0.1), but your browser can also send
requests on behalf of a website you have open. So:

1. Only the host names 127.0.0.1 and localhost (TrustedHostMiddleware), plus the ones
   you name in PROGEN_EXTRA_HOSTS (e.g. my-pc.tail1234.ts.net, when tailscale serve puts
   Progen on your private Tailscale network for your phone). A website can point its own
   name at 127.0.0.1 (DNS rebinding); then the name doesn't match and Progen refuses
   everything, reading included. Through an extra host you always have to sign in
   (app/auth.py); on this computer itself you don't.
2. Every change (POST, PUT, PATCH, DELETE) has to prove that it comes from Progen
   itself or your own script, with something another website can't send along
   without permission (CORS), and Progen doesn't give that permission:
     - HX-Request: true        (the web page, through htmx)
     - Content-Type: application/json   (the JSON API, scripts)
     - X-Progen-Shutdown       (Stop Progen)
3. If the browser says the request comes from another site (Origin or
   Sec-Fetch-Site), it is refused, even when 2 checks out. An Origin of one of
   the allowed hosts (an extra host too) is Progen itself.
"""
from urllib.parse import urlsplit

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import Auth, LoginMiddleware
from .desktop import SHUTDOWN_HEADER

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_own_change(headers: dict[str, str]) -> bool:
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()
    return (headers.get("hx-request") == "true"
            or content_type == "application/json"
            or headers.get(SHUTDOWN_HEADER.lower()) == "1")


def _from_another_site(headers: dict[str, str], allowed_hosts: list[str]) -> bool:
    if headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = headers.get("origin")
    if origin and origin != "null":
        return urlsplit(origin).hostname not in allowed_hosts
    return origin == "null"  # sandbox or file://: never trust


class CsrfMiddleware:
    """Refuses changes that don't provably come from Progen itself (see the top)."""

    def __init__(self, app: ASGIApp, allowed_hosts: list[str]):
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            if _from_another_site(headers, self.allowed_hosts) or not _is_own_change(headers):
                response = PlainTextResponse("Progen only accepts changes from its own pages and scripts.",
                                             status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def protect(app, allowed_hosts: list[str] = ALLOWED_HOSTS, auth: Auth | None = None) -> None:
    """Puts the protections on the app. Order: first the host name, then signing in (only with
    extra hosts), then CSRF. The last middleware added runs first."""
    app.add_middleware(CsrfMiddleware, allowed_hosts=allowed_hosts)
    if auth is not None and auth.enabled:
        app.add_middleware(LoginMiddleware, auth=auth)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
