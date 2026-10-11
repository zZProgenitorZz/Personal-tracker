"""Sunny in de app: het chatpaneel (htmx), Confirm/Cancel en Settings › Sunny.

Dunne routes: input lezen, Sunny (session.py) laten werken, renderen. Zonder ANTHROPIC_API_KEY
toont het paneel één nette regel in plaats van een fout.
"""
from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse

from ..domain import DomainError
from ..web import render, toast
from .agent import api_key
from .commands import ChangeAssistantModel
from .models import MODELS, model_by_id
from .session import Outcome, Sunny

CHANGED = "assistant-changed"


def create_assistant_router(sunny: Sunny) -> APIRouter:
    router = APIRouter(prefix="/ui/assistant", include_in_schema=False)

    def body(request: Request):
        return render(request, "_sunny_body.html", available=sunny.client is not None, entries=sunny.entries,
                      banner=sunny.standing_problem)

    def result(request: Request, outcome: Outcome):
        response = render(request, "_sunny_result.html", proposal=outcome.proposal, note=outcome.note,
                          toast=outcome.toast)
        if outcome.changed:
            response.headers["HX-Trigger"] = ", ".join(outcome.changed)
        return response

    @router.get("")
    def show(request: Request):
        return body(request)

    @router.post("/message")
    def message(request: Request, message: str = Form(""), retry: str = Form("")):
        """Een bericht van Progen. Met retry (de knop Try again) staat het bericht al in het gesprek."""
        text = message.strip()
        if sunny.client is None:
            return render(request, "_sunny_entries.html", entries=[{"kind": "unavailable"}])
        new = sunny.send(text, retry=bool(retry)) if text else []
        return render(request, "_sunny_entries.html", entries=new)

    @router.post("/confirm/{proposal_id}")
    def confirm(request: Request, proposal_id: str):
        return result(request, sunny.confirm(proposal_id))

    @router.post("/cancel/{proposal_id}")
    def cancel(request: Request, proposal_id: str):
        return result(request, sunny.cancel(proposal_id))

    @router.post("/reset")
    def reset(request: Request):
        sunny.reset()
        return body(request)

    # ---- Settings › Sunny: welk model ----

    @router.get("/settings")
    def settings_panel(request: Request):
        return render(request, "_sunny_settings.html", models=MODELS, current=sunny.settings.settings().model,
                      key_found=bool(api_key()))  # de key zelf nooit tonen

    @router.post("/model")
    def choose_model(request: Request, model: str = Form("")):
        try:
            sunny.settings.handle(ChangeAssistantModel(model))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        return toast(request, f"Sunny now uses {model_by_id(model).name}.", changed=CHANGED)

    @router.get("/model-label")
    def model_label():
        """De korte naam onder de kop van het chatpaneel, bv. "Haiku 5.5"."""
        return PlainTextResponse(model_by_id(sunny.settings.settings().model).short)

    return router
