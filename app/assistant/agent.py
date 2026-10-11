"""Sunny's loop: één bericht van Progen → hooguit MAX_ROUNDS rondes met Claude → een antwoord,
plus eventueel voorgestelde acties (die pas na Confirm worden uitgevoerd, zie web.py).

- Leestool → meteen uitvoeren, compact resultaat terug naar het model.
- Schrijftool → als PendingAction bewaren; het model hoort "proposed, waiting for Progen's
  confirmation".
- De geschiedenis gaat als platte tekst mee (alleen de laatste HISTORY_MESSAGES): geen
  denkblokken of tool-uitvoer van eerdere beurten. Zo mag de geschiedenis ingekort worden
  zonder dat de API een gewijzigde denkketen weigert, en blijft elk bericht goedkoop.
- Het vaste deel van de systeemprompt en de toollijst hebben cache_control.
"""
import json
import logging
import os
from dataclasses import dataclass, field

import anthropic

from .prompt import system_blocks
from .tools import TOOL_DEFINITIONS, PendingAction, ToolError, TrackerData, run_tool

MODEL = "claude-haiku-5-5"   # later bijvoorbeeld "claude-sonnet-5-5" voor moeilijkere vragen
EFFORT = "low"
MAX_TOKENS = 1024
MAX_ROUNDS = 5
HISTORY_MESSAGES = 10

log = logging.getLogger("progen.assistant")


def make_client() -> anthropic.Anthropic | None:
    """Een client als ANTHROPIC_API_KEY in .env (of de omgeving) staat; anders None."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
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
    error: bool = False


class Agent:
    def __init__(self, client, data: TrackerData):
        self._client = client
        self._data = data

    def ask(self, message: str, history: list[dict]) -> Reply:
        messages = trim_history(history) + [{"role": "user", "content": message}]
        actions: list[PendingAction] = []
        response = None
        try:
            for _ in range(MAX_ROUNDS):
                response = self._client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS, output_config={"effort": EFFORT},
                    system=system_blocks(self._data.now().date()), tools=TOOL_DEFINITIONS, messages=messages)
                if response.stop_reason != "tool_use":
                    break
                messages.append({"role": "assistant", "content": response.content})  # ongewijzigd terug
                messages.append({"role": "user", "content": [self._run(block, actions)
                                                              for block in response.content
                                                              if block.type == "tool_use"]})
        except anthropic.AuthenticationError:
            return Reply("The key in .env was refused. Check ANTHROPIC_API_KEY, Progen.", error=True)
        except anthropic.RateLimitError:
            return Reply("Too many questions at once. Give it a minute.", error=True)
        except anthropic.APIConnectionError:
            return Reply("I can't reach Anthropic right now. Is the internet on?", error=True)
        except anthropic.APIStatusError as exc:
            log.warning("Sunny: API error %s", exc.status_code)
            return Reply(f"Something went wrong on Anthropic's side ({exc.status_code}). Try again.", error=True)

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

