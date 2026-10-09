"""Netjes afsluiten vanaf deze computer (voor "Stop Progen", zie stop.pyw).

De server stopt via een gewone Ctrl+C (SIGINT) aan zichzelf: uvicorn rondt
lopende verzoeken af, de achtergrondtaak stopt en de database wordt gesloten.
Uvicorn wacht daarbij hooguit --timeout-graceful-shutdown seconden (zie launch.pyw).
Leeft het proces daarna nog steeds, dan stopt het hard (vangnet): elke schrijfactie
in de event store is dan al afgerond, dus er gaat niets verloren.

Alleen toegestaan vanaf 127.0.0.1 én met de header X-Progen-Shutdown. Die header
houdt websites in je browser tegen: die kunnen wel naar 127.0.0.1 posten, maar
geen eigen header meesturen zonder toestemming (CORS), en die geeft Progen niet.
"""
import logging
import os
import signal
import threading

from fastapi import APIRouter, Request, Response

from .desktop import SHUTDOWN_HEADER

LOCAL_HOSTS = {"127.0.0.1", "::1"}
FORCE_EXIT_SECONDS = 20  # ruim boven de 5 seconden die uvicorn krijgt om netjes te stoppen

log = logging.getLogger("progen.shutdown")


def force_exit() -> None:
    log.error("Progen did not stop within %s seconds; stopping hard.", FORCE_EXIT_SECONDS)
    logging.shutdown()
    os._exit(0)


def stop_this_server() -> None:
    """Even wachten zodat het antwoord nog verstuurd wordt, dan Ctrl+C naar onszelf.
    Daarnaast een vangnet: een daemon-timer die het proces hard stopt als het blijft hangen.
    Stopt de server gewoon, dan verdwijnt die timer vanzelf mee."""
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
