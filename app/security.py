"""Bescherming tegen andere websites: CSRF en DNS rebinding.

Progen draait alleen op je eigen computer, maar je browser kan ook verzoeken
sturen namens een website die je open hebt. Daarom:

1. Alleen de hostnamen 127.0.0.1 en localhost (TrustedHostMiddleware). Een
   website kan zijn eigen naam naar 127.0.0.1 laten wijzen (DNS rebinding);
   dan klopt de naam niet en weigert Progen alles, ook lezen.
2. Elke wijziging (POST, PUT, PATCH, DELETE) moet bewijzen dat hij van Progen
   zelf of een eigen script komt, met iets wat een andere website niet zonder
   toestemming (CORS) kan meesturen, en die toestemming geeft Progen niet:
     - HX-Request: true        (de webpagina, via htmx)
     - Content-Type: application/json   (de JSON-API, scripts)
     - X-Progen-Shutdown       (Stop Progen)
3. Zegt de browser dat het verzoek van een andere site komt (Origin of
   Sec-Fetch-Site), dan wordt het geweigerd, ook als 2 klopt.
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
    return origin == "null"  # sandbox of file://: nooit vertrouwen


class CsrfMiddleware:
    """Weigert wijzigingen die niet aantoonbaar van Progen zelf komen (zie bovenaan)."""

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
    """Zet beide beschermingen op de app. Volgorde: eerst de hostnaam, dan CSRF."""
    app.add_middleware(CsrfMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
