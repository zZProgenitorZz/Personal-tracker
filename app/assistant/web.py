"""Sunny in the app: the chat panel (htmx), Confirm/Cancel and Settings › Sunny.

Thin routes: read the input, let Sunny (session.py) do the work, render. Without ANTHROPIC_API_KEY
the panel shows one tidy line instead of an error (quick chapter logs still work: they need no API).
"""
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..domain import DomainError
from ..web import render, templates, toast
from .agent import PROBLEM_TEXTS, SunnyProblem, api_key
from .commands import ChangeAssistantModel, ChangeQuickSaves, RemoveAlias
from .models import MODELS, model_by_id
from .session import Outcome, Sunny

CHANGED = "assistant-changed"
MAX_ALTS = 2  # what else the recognizer heard, at most
# The panel shows this balloon itself when the connection to Progen drops halfway through an answer.
OFFLINE = SunnyProblem("offline", PROBLEM_TEXTS["offline"])


def _line(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


def _done(entries: list[dict]) -> str:
    """The new entries, rendered as everywhere, and the events to send (e.g. reading-changed after a quick log)."""
    changed = sorted({entry["changed"] for entry in entries if entry.get("changed")})
    return _line({"t": "done", "html": templates.get_template("_sunny_entries.html").render(entries=entries),
                  "changed": changed})


def _alts(raw: str, text: str) -> tuple[str, ...]:
    """The recognizer's other guesses: a JSON list, only the ones that differ, at most MAX_ALTS."""
    try:
        guesses = json.loads(raw) if raw else []
    except ValueError:
        return ()
    found: list[str] = []
    for guess in guesses if isinstance(guesses, list) else []:
        guess = str(guess).strip()
        if guess and guess.lower() != text.lower() and guess not in found:
            found.append(guess)
    return tuple(found[:MAX_ALTS])


def create_assistant_router(sunny: Sunny) -> APIRouter:
    router = APIRouter(prefix="/ui/assistant", include_in_schema=False)

    def body(request: Request):
        return render(request, "_sunny_body.html", available=sunny.client is not None, entries=sunny.entries,
                      banner=sunny.standing_problem, offline=OFFLINE,
                      quick_saves=sunny.settings.settings().quick_saves)

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
    def stream(message: str = Form(""), retry: str = Form(""), voice: str = Form(""), alts: str = Form("")):
        """A message from Progen, answered as it comes: one JSON line per event (NDJSON),
        {"t": "text"|"status", "v": ...}, and last {"t": "done", "html": the new entries, rendered as
        everywhere}. With retry (the Try again button) the message is already in the conversation;
        with voice it was spoken (hold to talk) and the answer will be read aloud; alts (JSON) is what
        else the recognizer heard. Without a key only a quick chapter log works.
        A POST, so the CSRF check applies: the panel sends HX-Request."""
        text = message.strip()
        heard = _alts(alts, text) if voice == "1" else ()

        def lines():
            if not text:
                yield _done([])
                return
            for kind, value in sunny.send_stream(text, retry=bool(retry), voice=voice == "1", alts=heard):
                yield _done(value) if kind == "entries" else _line({"t": kind, "v": value})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @router.post("/confirm/{proposal_id}")
    def confirm(request: Request, proposal_id: str):
        return result(request, sunny.confirm(proposal_id))

    @router.post("/cancel/{proposal_id}")
    def cancel(request: Request, proposal_id: str):
        return result(request, sunny.cancel(proposal_id))

    @router.post("/undo/{undo_id}")
    def undo(request: Request, undo_id: str):
        """Sets a chapter saved right away back; the button goes (out of band), Sunny's line is added."""
        note = sunny.undo(undo_id)
        if note is None:
            return toast(request, "Nothing to undo any more.", error=True)
        response = render(request, "_sunny_undone.html", note=note, undo_id=undo_id)
        response.headers["HX-Trigger"] = "reading-changed"
        return response

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
        settings = sunny.settings.settings()
        aliases = sunny.data.aliases.all() if sunny.data.aliases is not None else []
        library = sunny.data.library
        nicknames = [(heard, library.get(sid).title if library.get(sid) else "a removed series")
                     for heard, sid in aliases]
        return render(request, "_sunny_settings.html", models=MODELS, current=settings.model,
                      quick_saves=settings.quick_saves, nicknames=nicknames,
                      key_found=bool(api_key()))  # never show the key itself

    @router.post("/quick-saves")
    def quick_saves(request: Request, enabled: str = Form("")):
        sunny.settings.handle(ChangeQuickSaves(bool(enabled)))
        message = "Chapter updates are saved right away, with Undo." if enabled else "Chapter updates wait for Confirm."
        return toast(request, message, changed=CHANGED)

    @router.post("/aliases/remove")
    def remove_alias(request: Request, heard: str = Form("")):
        try:
            sunny.settings.handle(RemoveAlias(heard))
        except DomainError as exc:
            return toast(request, str(exc), error=True)
        return toast(request, f'Forgot the nickname "{heard}".', changed=CHANGED)

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
