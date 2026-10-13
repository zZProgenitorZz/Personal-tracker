"""The login page and logout, for requests through an extra host (see app/auth.py).

The form posts through htmx, so it passes the same CSRF check as every other change. A wrong
password re-renders the form; the right one sets the session cookie and sends you on (HX-Redirect).
"""
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from .auth import Auth, headers_of, is_https, session_cookie
from .web import render


def safe_next(target: str) -> str:
    """Only a path in Progen itself, so the login page can't send you to another site."""
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_login_router(auth: Auth) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    def form(request: Request, next_url: str, error: str | None = None):
        return render(request, "_login_form.html", next=next_url, error=error)

    @router.get("/login")
    def login_page(request: Request, next: str = "/"):
        return render(request, "login.html", next=safe_next(next), error=None)

    @router.post("/login")
    def log_in(request: Request, password: str = Form(""), next: str = Form("/")):
        next_url = safe_next(next)
        wait = auth.wait_seconds(_client(request))
        if wait:
            minutes = -(-wait // 60)
            return form(request, next_url, f"Too many wrong tries. Try again in {minutes} "
                                           f"minute{'s' if minutes != 1 else ''}.")
        if not auth.enabled or not auth.check_password(password):
            auth.failed(_client(request))
            return form(request, next_url, "That's not the password.")
        auth.succeeded(_client(request))
        secure = is_https(request.scope, headers_of(request.scope))
        return HTMLResponse("", headers={"HX-Redirect": next_url,
                                         "Set-Cookie": session_cookie(auth.new_session(), secure)})

    @router.post("/logout")
    def log_out(request: Request):
        secure = is_https(request.scope, headers_of(request.scope))
        return HTMLResponse("", headers={"HX-Redirect": "/login", "Set-Cookie": session_cookie("", secure, 0)})

    return router
