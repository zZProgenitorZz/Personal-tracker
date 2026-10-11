"""Sunny in the app: the chat panel (htmx), Confirm/Cancel and Settings › Sunny.

Thin routes: read the input, let Sunny (session.py) do the work, render. Without ANTHROPIC_API_KEY
the panel shows one tidy line instead of an error.
"""
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..domain import DomainError
from ..web import render, templates, toast
from .agent import PROBLEM_TEXTS, SunnyProblem, api_key
from .commands import ChangeAssistantModel
from .models import MODELS, model_by_id
from .session import Outcome, Sunny

CHANGED = "assistant-changed"
# The panel shows this balloon itself when the connection to Progen drops halfway through an answer.
OFFLINE = SunnyProblem("offline", PROBLEM_TEXTS["offline"])


def _line(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


def _done(entries: list[dict]) -> str:
    return _line({"t": "done", "html": templates.get_template("_sunny_entries.html").render(entries=entries)})


def create_assistant_router(sunny: Sunny) -> APIRouter:
    router = APIRouter(prefix="/ui/assistant", include_in_schema=False)

    def body(request: Request):
        return render(request, "_sunny_body.html", available=sunny.client is not None, entries=sunny.entries,
                      banner=sunny.standing_problem, offline=OFFLINE)

    def result(request: Request, outcome: Outcome):
        response = render(request, "_sunny_result.html", proposal=outcome.proposal, note=outcome.note,
                          toast=outcome.toast)
        if outcome.changed:
            response.headers["HX-Trigger"] = ", ".join(outcome.changed)
        return response

    @router.get("")
    def show(request: Request):
        return body(request)

    @router.post("/stream")
    def stream(message: str = Form(""), retry: str = Form("")):
        """A message from Progen, answered as it comes: one JSON line per event (NDJSON),
        {"t": "text"|"status", "v": ...}, and last {"t": "done", "html": the new entries, rendered as
        everywhere}. With retry (the Try again button) the message is already in the conversation.
        A POST, so the CSRF check applies: the panel sends HX-Request."""
        text = message.strip()

        def lines():
            if sunny.client is None:
                yield _done([{"kind": "unavailable"}])
            elif not text:
                yield _done([])
            else:
                for kind, value in sunny.send_stream(text, retry=bool(retry)):
                    yield _done(value) if kind == "entries" else _line({"t": kind, "v": value})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @router.post("/confirm/{proposal_id}")
    def confirm(request: Request, proposal_id: str):
        return result(request, sunny.confirm(proposal_id))

    @router.post("/cancel/{proposal_id}")
    def cancel(request: Request, proposal_id: str):
        return result(request, sunny.cancel(proposal_id))

    @router.get("/notices")
    def notices(request: Request):
        """The block at the top of the panel, plus the badge on the button (out of band)."""
        return render(request, "_sunny_notices.html", notices=sunny.notices())

    @router.post("/notices/dismiss")
    def dismiss(request: Request, key: str = Form("")):
        sunny.dismiss(key)
        return render(request, "_sunny_notices.html", notices=sunny.notices())

    @router.post("/reset")
    def reset(request: Request):
        sunny.reset()
        return body(request)

    # ---- Settings › Sunny: which model ----

    @router.get("/settings")
    def settings_panel(request: Request):
        return render(request, "_sunny_settings.html", models=MODELS, current=sunny.settings.settings().model,
                      key_found=bool(api_key()))  # never show the key itself

    @router.post("/model")
    def choose_model(request: Request, model: str = Form("")):
        try:
            sunny.settings.handle(ChangeAssistantModel(model))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        return toast(request, f"Sunny now uses {model_by_id(model).name}.", changed=CHANGED)

    @router.get("/model-label")
    def model_label():
        """The short name below the chat panel's heading, e.g. "Haiku 5.5"."""
        return PlainTextResponse(model_by_id(sunny.settings.settings().model).short)

    return router
