"""A fake Anthropic client for the tests: never talks to the real API.

You pass a list of responses; each messages.stream streams the next one and remembers what
was asked (model, system, tools, messages), so the tests can check that. A response may also be
an exception: it is raised from the stream, after the text that comes before it (see failing()).
"""
import copy
import json
import re
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


def failing(error, *blocks):
    """A response that streams `blocks` first and then breaks with `error`, like a dropped API call."""
    return SimpleNamespace(content=list(blocks), stop_reason=None, error=error)


class FakeStream:
    """Like the SDK's MessageStream: a context manager that yields events, then the final message.
    Text comes word by word, as ("text", delta) events after a content_block_start."""

    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.response.content:
            yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type=block.type))
            if block.type == "text":
                for word in re.findall(r"\S+\s*|\s+", block.text):
                    yield SimpleNamespace(type="text", text=word, snapshot="")
        if getattr(self.response, "error", None) is not None:
            raise self.response.error

    def get_final_message(self):
        return self.response


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **request):
        self.calls.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("the fake client ran out of answers")
        return FakeStream(self.responses.pop(0))


def say(client, message: str, retry: bool = False) -> str:
    """Posts to /ui/assistant/stream like the panel does and returns the html of the last ("done") line."""
    lines = stream_lines(client, message, retry)
    assert lines[-1]["t"] == "done" and all(line["t"] != "done" for line in lines[:-1])
    return lines[-1]["html"]


def stream_lines(client, message: str, retry: bool = False) -> list[dict]:
    reply = client.post("/ui/assistant/stream", data={"message": message, "retry": "1" if retry else ""})
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in reply.text.splitlines() if line.strip()]
