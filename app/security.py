"""Protection against other websites: CSRF and DNS rebinding.

Progen only runs on your own computer, but your browser can also send requests
on behalf of a website you have open. So:

1. Only the host names 127.0.0.1 and localhost (TrustedHostMiddleware). A
   website can point its own name at 127.0.0.1 (DNS rebinding); then the
   name doesn't match and Progen refuses everything, reading included.
2. Every change (POST, PUT, PATCH, DELETE) has to prove that it comes from Progen
   itself or your own script, with something another website can't send along
   without permission (CORS), and Progen doesn't give that permission:
     - HX-Request: true        (the web page, through htmx)
     - Content-Type: application/json   (the JSON API, scripts)
     - X-Progen-Shutdown       (Stop Progen)
3. If the browser says the request comes from another site (Origin or
   Sec-Fetch-Site), it is refused, even when 2 checks out.
"""
from urllib.parse import urlsplit

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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


def protect(app, allowed_hosts: list[str] = ALLOWED_HOSTS) -> None:
    """Puts both protections on the app. Order: first the host name, then CSRF."""
    app.add_middleware(CsrfMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
