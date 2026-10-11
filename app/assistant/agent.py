"""Sunny's loop: one message from Progen → at most MAX_ROUNDS rounds with Claude → an answer,
plus any proposed actions (which only run after Confirm, see web.py).

- Read tool → run it right away, a compact result back to the model.
- Write tool → keep it as a PendingAction; the model hears "proposed, waiting for Progen's
  confirmation".
- The history goes along as plain text (only the last HISTORY_MESSAGES): no
  thinking blocks or tool output from earlier turns. That way the history may be shortened
  without the API refusing a changed chain of thought, and every message stays cheap.
- The fixed part of the system prompt and the tool list have cache_control.
- You pick the model in Settings › Sunny (AssistantSettings); it is read with every message,
  so a change applies right away. Effort and max_tokens are the same for every model.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import anthropic

from .aggregate import AssistantSettings
from .prompt import system_blocks
from .tools import TOOL_DEFINITIONS, PendingAction, ToolError, TrackerData, run_tool

EFFORT = "low"
MAX_TOKENS = 1024
MAX_ROUNDS = 5
HISTORY_MESSAGES = 10

log = logging.getLogger("progen.assistant")

# Search words in Anthropic's error message (lower case). If Anthropic changes the text,
# you only need to update these two lists.
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
# What the thinking bubble says while a tool runs (one line per tool); others get DEFAULT_STATUS.
TOOL_STATUS = {
    "find_item": "Looking for it…",
    "library": "Checking your library…",
    "listening_stats": "Counting your plays…",
    "set_status": "Lining that up…",
    "add_item": "Lining that up…",
    "plans_between": "Checking your planner…",
    "history_between": "Looking through your history…",
    "item_history": "Looking through your history…",
    "notices": "Checking what needs you…",
}
DEFAULT_STATUS = "Looking that up…"
CONSOLE_KINDS = {"auth", "credits", "limit"}       # get an "Open console" button
STICKY_KINDS = {"auth", "permission", "credits", "limit"}  # stay visible when opening until a call succeeds


@dataclass(frozen=True)
class SunnyProblem:
    kind: str           # auth / permission / credits / limit / rate / overloaded / offline / unknown
    text: str           # what Sunny says

    @property
    def console(self) -> bool:
        return self.kind in CONSOLE_KINDS

    @property
    def sticky(self) -> bool:
        return self.kind in STICKY_KINDS


def _problem(kind: str, extra: str = "") -> SunnyProblem:
    return SunnyProblem(kind, PROBLEM_TEXTS[kind] + extra)


def money_problem(message: str) -> str | None:
    """"credits" or "limit" if the error message is about that, otherwise None."""
    text = message.lower()
    if any(word in text for word in CREDIT_WORDS):
        return "credits"
    if any(word in text for word in SPEND_LIMIT_WORDS):
        return "limit"
    return None


def _api_message(exc: anthropic.APIStatusError) -> str:
    """The API's own message (body.error.message), otherwise the exception's text."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    return str(error.get("message") or exc.message or "")


def classify(exc: Exception) -> SunnyProblem:
    """Translates an error from the anthropic SDK into what Sunny says. Most specific class first.
    The SDK already retries 429 and 5xx itself (max_retries); this is what remains."""
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
    elif isinstance(exc, anthropic.APIConnectionError):  # APITimeoutError too
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
    """ANTHROPIC_API_KEY from .env (or the environment), or "" if it isn't there."""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def make_client() -> anthropic.Anthropic | None:
    """A client if there is an API key; otherwise None."""
    key = api_key()
    return anthropic.Anthropic(api_key=key) if key else None


def trim_history(history: list[dict]) -> list[dict]:
    """The last HISTORY_MESSAGES messages, starting at a message from Progen."""
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
        self._settings = settings or (lambda: AssistantSettings([]))  # without a setting: the default model

    def ask(self, message: str, history: list[dict]) -> Reply:
        """The whole answer at once: ask_stream() read to the end."""
        return next(value for kind, value in self.ask_stream(message, history) if kind == "reply")

    def ask_stream(self, message: str, history: list[dict]) -> Iterator[tuple[str, Any]]:
        """Sunny's answer as it comes: ("text", delta) for every piece of text, ("status", line) for
        every tool in a tool round, and once at the end ("reply", Reply). After an API error the
        reply carries the problem; any text streamed before it isn't part of that reply."""
        messages = trim_history(history) + [{"role": "user", "content": message}]
        actions: list[PendingAction] = []
        texts: list[str] = []  # every text block of every round: the reply text is what was streamed
        response = None
        model = self._settings().model  # again with every message: a choice in Settings applies right away
        try:
            for _ in range(MAX_ROUNDS):
                response = yield from self._round(model, messages, texts)
                if response.stop_reason != "tool_use":
                    break
                tools = [block for block in response.content if block.type == "tool_use"]
                for block in tools:
                    yield "status", TOOL_STATUS.get(block.name, DEFAULT_STATUS)
                messages.append({"role": "assistant", "content": response.content})  # returned unchanged
                messages.append({"role": "user", "content": [self._run(block, actions) for block in tools]})
        except anthropic.APIError as exc:  # everything from the SDK: status, connection and timeout errors
            problem = classify(exc)
            yield "reply", Reply(problem.text, actions, problem)
            return
        yield "reply", self._reply(response, "\n".join(texts).strip(), actions)

    def _round(self, model: str, messages: list, texts: list[str]):
        """One call to the API, streamed. Yields the text deltas, returns the final message."""
        with self._client.messages.stream(
                model=model, max_tokens=MAX_TOKENS, output_config={"effort": EFFORT},
                system=system_blocks(self._data.now().date()), tools=TOOL_DEFINITIONS, messages=messages) as stream:
            for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "text" and texts:
                    yield "text", "\n"  # a new text block: the same line break as in the reply text
                elif event.type == "text":
                    yield "text", event.text
            response = stream.get_final_message()
        texts += [b.text for b in response.content if b.type == "text"]
        return response

    @staticmethod
    def _reply(response, text: str, actions: list[PendingAction]) -> Reply:
        if response.stop_reason == "refusal":
            return Reply("That's not something I'll help with.", actions)
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

