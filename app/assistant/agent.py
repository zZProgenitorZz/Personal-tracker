"""Sunny's loop: één bericht van Progen → hooguit MAX_ROUNDS rondes met Claude → een antwoord,
plus eventueel voorgestelde acties (die pas na Confirm worden uitgevoerd, zie web.py).

- Leestool → meteen uitvoeren, compact resultaat terug naar het model.
- Schrijftool → als PendingAction bewaren; het model hoort "proposed, waiting for Progen's
  confirmation".
- De geschiedenis gaat als platte tekst mee (alleen de laatste HISTORY_MESSAGES): geen
  denkblokken of tool-uitvoer van eerdere beurten. Zo mag de geschiedenis ingekort worden
  zonder dat de API een gewijzigde denkketen weigert, en blijft elk bericht goedkoop.
- Het vaste deel van de systeemprompt en de toollijst hebben cache_control.
- Het model kies je in Settings › Sunny (AssistantSettings); het wordt bij elk bericht gelezen,
  zodat een wijziging meteen geldt. Effort en max_tokens zijn voor elk model gelijk.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from .aggregate import AssistantSettings
from .prompt import system_blocks
from .tools import TOOL_DEFINITIONS, PendingAction, ToolError, TrackerData, run_tool

EFFORT = "low"
MAX_TOKENS = 1024
MAX_ROUNDS = 5
HISTORY_MESSAGES = 10

log = logging.getLogger("progen.assistant")

# Zoekwoorden in de foutmelding van Anthropic (kleine letters). Verandert Anthropic de tekst,
# dan hoef je alleen deze twee lijsten bij te werken.
CREDIT_WORDS = ("credit balance", "purchase credits")
SPEND_LIMIT_WORDS = ("usage limit", "spend limit", "spending limit", "monthly limit")

PROBLEM_TEXTS = {
    "auth": "My key expired. Or someone revoked it. Make a new one at console.anthropic.com, put it in .env as "
            "ANTHROPIC_API_KEY and restart the app.",
    "permission": "This key isn't allowed to do that. Check the key's workspace and permissions in the console.",
    "credits": "I'm out of credits. Top up at console.anthropic.com → Billing, then try again.",
    "limit": "You hit the monthly limit you set. Raise it in the console, or wait until next month.",
    "rate": "Too many requests at once. Give me a few seconds.",
    "overloaded": "Anthropic's servers are having a bad day. Try again in a moment.",
    "offline": "I can't reach Anthropic. Is the internet on?",
}
CONSOLE_KINDS = {"auth", "credits", "limit"}       # krijgen een knop "Open console"
STICKY_KINDS = {"auth", "permission", "credits", "limit"}  # blijven zichtbaar bij openen tot een aanroep lukt


@dataclass(frozen=True)
class SunnyProblem:
    kind: str           # auth / permission / credits / limit / rate / overloaded / offline / unknown
    text: str           # wat Sunny zegt

    @property
    def console(self) -> bool:
        return self.kind in CONSOLE_KINDS

    @property
    def sticky(self) -> bool:
        return self.kind in STICKY_KINDS


def _problem(kind: str, extra: str = "") -> SunnyProblem:
    return SunnyProblem(kind, PROBLEM_TEXTS[kind] + extra)


def money_problem(message: str) -> str | None:
    """"credits" of "limit" als de foutmelding daarover gaat, anders None."""
    text = message.lower()
    if any(word in text for word in CREDIT_WORDS):
        return "credits"
    if any(word in text for word in SPEND_LIMIT_WORDS):
        return "limit"
    return None


def _api_message(exc: anthropic.APIStatusError) -> str:
    """De melding van de API zelf (body.error.message), anders de tekst van de exception."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    return str(error.get("message") or exc.message or "")


def classify(exc: Exception) -> SunnyProblem:
    """Vertaal een fout van de anthropic-SDK naar wat Sunny zegt. Meest specifieke klasse eerst.
    429 en 5xx probeert de SDK zelf al opnieuw (max_retries); dit is wat overblijft."""
    if isinstance(exc, anthropic.AuthenticationError):
        return _problem("auth")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return _problem("permission")
    if isinstance(exc, anthropic.BadRequestError):
        if kind := money_problem(_api_message(exc)):
            return _problem(kind)
    elif isinstance(exc, anthropic.RateLimitError):
        if money_problem(_api_message(exc)) == "limit":
            return _problem("limit")
        wait = exc.response.headers.get("retry-after", "").strip()
        return _problem("rate", f" (Anthropic says: try again in {wait}s.)" if wait.isdigit() else "")
    elif isinstance(exc, (anthropic.OverloadedError, anthropic.InternalServerError)):
        return _problem("overloaded")
    elif isinstance(exc, anthropic.APIConnectionError):  # ook APITimeoutError
        return _problem("offline")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 402 or exc.type == "billing_error":
            return _problem("credits")
        if exc.status_code >= 500:
            return _problem("overloaded")
        status = exc.status_code
    else:
        status = "unknown"
    log.error("Sunny: unexpected error from Anthropic: %r", exc, exc_info=exc)
    return SunnyProblem("unknown", f"Something went wrong talking to Anthropic (status {status}).")


def api_key() -> str:
    """ANTHROPIC_API_KEY uit .env (of de omgeving), of "" als die er niet is."""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def make_client() -> anthropic.Anthropic | None:
    """Een client als er een API-key is; anders None."""
    key = api_key()
    return anthropic.Anthropic(api_key=key) if key else None


def trim_history(history: list[dict]) -> list[dict]:
    """De laatste HISTORY_MESSAGES berichten, beginnend bij een bericht van Progen."""
    trimmed = history[-HISTORY_MESSAGES:]
    while trimmed and trimmed[0]["role"] != "user":
        trimmed = trimmed[1:]
    return trimmed


@dataclass
class Reply:
    text: str
    actions: list[PendingAction] = field(default_factory=list)
    problem: SunnyProblem | None = None

    @property
    def error(self) -> bool:
        return self.problem is not None


class Agent:
    def __init__(self, client, data: TrackerData, settings: Callable[[], AssistantSettings] | None = None):
        self._client = client
        self._data = data
        self._settings = settings or (lambda: AssistantSettings([]))  # zonder instelling: het standaardmodel

    def ask(self, message: str, history: list[dict]) -> Reply:
        messages = trim_history(history) + [{"role": "user", "content": message}]
        actions: list[PendingAction] = []
        response = None
        model = self._settings().model  # bij elk bericht opnieuw: een keuze in Settings geldt meteen
        try:
            for _ in range(MAX_ROUNDS):
                response = self._client.messages.create(
                    model=model, max_tokens=MAX_TOKENS, output_config={"effort": EFFORT},
                    system=system_blocks(self._data.now().date()), tools=TOOL_DEFINITIONS, messages=messages)
                if response.stop_reason != "tool_use":
                    break
                messages.append({"role": "assistant", "content": response.content})  # ongewijzigd terug
                messages.append({"role": "user", "content": [self._run(block, actions)
                                                              for block in response.content
                                                              if block.type == "tool_use"]})
        except anthropic.APIError as exc:  # alles van de SDK: status-, verbindings- en timeoutfouten
            problem = classify(exc)
            return Reply(problem.text, actions, problem)

        if response.stop_reason == "refusal":
            return Reply("That's not something I'll help with.", actions)
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "tool_use":
            text = text or "That took me too many steps. Say it a bit simpler?"
        elif not text:
            text = "Done looking." if not actions else "Here's what I'd change."
        return Reply(text, actions)

    def _run(self, block, actions: list[PendingAction]) -> dict:
        try:
            result, pending = run_tool(block.name, dict(block.input), self._data)
        except ToolError as exc:
            return {"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True}
        if pending is not None:
            actions.append(pending)
            content = result
        else:
            content = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}

