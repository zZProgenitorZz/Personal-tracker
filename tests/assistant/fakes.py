"""Een nep-Anthropic-client voor de tests: praat nooit met de echte API.

Je geeft een lijst antwoorden mee; elke messages.create geeft de volgende terug en onthoudt wat
er gevraagd werd (model, system, tools, messages), zodat de tests dat kunnen controleren.
"""
import copy
from types import SimpleNamespace


def text(value: str):
    return SimpleNamespace(type="text", text=value)


def tool(name: str, tool_input: dict, tool_id: str | None = None):
    return SimpleNamespace(type="tool_use", id=tool_id or f"toolu_{name}", name=name, input=tool_input)


def thinking():
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


def response(*blocks, stop_reason: str | None = None):
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **request):
        self.calls.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("the fake client ran out of answers")
        return self.responses.pop(0)
