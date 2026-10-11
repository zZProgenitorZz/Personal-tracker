"""Shutting down cleanly from this computer (for "Stop Progen", see stop.pyw).

The server stops through a plain Ctrl+C (SIGINT) to itself: uvicorn finishes
running requests, the background task stops and the database is closed.
Uvicorn waits at most --timeout-graceful-shutdown seconds for that (see launch.pyw).
If the process is still alive after that, it stops hard (a safety net): every write
to the event store has already finished by then, so nothing is lost.

Only allowed from 127.0.0.1 and with the header X-Progen-Shutdown. That header
stops websites in your browser: they can post to 127.0.0.1, but they can't
send their own header without permission (CORS), and Progen doesn't give it.
"""
import logging
import os
import signal
import threading

from fastapi import APIRouter, Request, Response

from .desktop import SHUTDOWN_HEADER

LOCAL_HOSTS = {"127.0.0.1", "::1"}
FORCE_EXIT_SECONDS = 20  # well above the 5 seconds uvicorn gets to stop cleanly

log = logging.getLogger("progen.shutdown")


def force_exit() -> None:
    log.error("Progen did not stop within %s seconds; stopping hard.", FORCE_EXIT_SECONDS)
    logging.shutdown()
    os._exit(0)


def stop_this_server() -> None:
    """Waits a moment so the response still gets sent, then Ctrl+C to ourselves.
    Also a safety net: a daemon timer that stops the process hard if it hangs.
    If the server just stops, that timer goes away with it."""
    threading.Timer(0.3, signal.raise_signal, [signal.SIGINT]).start()
    safety_net = threading.Timer(FORCE_EXIT_SECONDS, force_exit)
    safety_net.daemon = True
    safety_net.start()


def create_shutdown_router() -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.post("/admin/shutdown")
    def shutdown(request: Request):
        host = request.client.host if request.client else ""
        if host not in LOCAL_HOSTS or request.headers.get(SHUTDOWN_HEADER) != "1":
            return Response("Shutting down is only allowed from this computer.", status_code=403)
        request.app.state.request_shutdown()
        return Response("Progen is stopping.", status_code=202)

    return router
