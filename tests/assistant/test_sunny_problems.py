"""Sunny when there are problems with Anthropic's API: a clear balloon, no crash.

The errors are made with the anthropic SDK's exception classes and a fake HTTP response;
the real API is never called.
"""
import datetime as dt
import logging
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fakes import FakeClient, response, say, text, tool
from fastapi.testclient import TestClient

import app.clock as clock
from app.assistant.agent import Agent, classify
from app.main import create_app

URL = "https://api.anthropic.com/v1/messages"


def status_error(cls, status, message, headers=None, error_type="invalid_request_error"):
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    reply = httpx2.Response(status, request=httpx2.Request("POST", URL), headers=headers or {}, json=body)
    return cls(f"Error code: {status} - {body}", response=reply, body=body)


def connection_error(cls=anthropic.APIConnectionError):
    request = httpx2.Request("POST", URL)
    return cls(request=request)


CREDITS = "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."
LIMIT = "You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC."

CASES = [
    ("auth", status_error(anthropic.AuthenticationError, 401, "invalid x-api-key", error_type="authentication_error"),
     "My key expired. Or someone revoked it. Make a new one at console.anthropic.com, put it in .env as "
     "ANTHROPIC_API_KEY and restart the app."),
    ("permission", status_error(anthropic.PermissionDeniedError, 403, "not allowed", error_type="permission_error"),
     "This key isn't allowed to do that. Check the key's workspace and permissions in the console."),
    ("credits", status_error(anthropic.BadRequestError, 400, CREDITS),
     "I'm out of credits. Top up at console.anthropic.com → Billing, then try again."),
    ("limit", status_error(anthropic.BadRequestError, 400, LIMIT),
     "You hit the monthly limit you set. Raise it in the console, or wait until next month."),
    ("limit", status_error(anthropic.RateLimitError, 429, LIMIT, error_type="rate_limit_error"),
     "You hit the monthly limit you set. Raise it in the console, or wait until next month."),
    ("rate", status_error(anthropic.RateLimitError, 429, "Number of request tokens has exceeded your per-minute rate limit",
                          error_type="rate_limit_error"),
     "Too many requests at once. Give me a few seconds."),
    ("overloaded", status_error(anthropic.InternalServerError, 500, "Internal server error", error_type="api_error"),
     "Anthropic's servers are having a bad day. Try again in a moment."),
    ("overloaded", status_error(anthropic.OverloadedError, 529, "Overloaded", error_type="overloaded_error"),
     "Anthropic's servers are having a bad day. Try again in a moment."),
    ("overloaded", status_error(anthropic.APIStatusError, 503, "Service unavailable", error_type="api_error"),
     "Anthropic's servers are having a bad day. Try again in a moment."),
    ("offline", connection_error(), "I can't reach Anthropic. Is the internet on?"),
    ("offline", connection_error(anthropic.APITimeoutError), "I can't reach Anthropic. Is the internet on?"),
]


class Raises:
    """A fake client that raises the same error on every call."""

    def __init__(self, error):
        self.error = error
        self.messages = self

    def stream(self, **request):
        raise self.error


# On an error the agent only reads the date (for the system prompt); nothing more is needed.
NO_DATA = SimpleNamespace(now=lambda: dt.datetime(2026, 10, 11, 10, 0))


# ---- Every row from the table ----

@pytest.mark.parametrize("kind, error, message", CASES, ids=[f"{c[0]}-{type(c[1]).__name__}" for c in CASES])
def test_every_problem_gets_its_own_kind_and_words(kind, error, message):
    problem = classify(error)
    assert problem.kind == kind and problem.text == message


@pytest.mark.parametrize("kind, error, message", CASES[:3], ids=["auth", "permission", "credits"])
def test_the_agent_turns_the_error_into_a_reply_instead_of_crashing(kind, error, message):
    reply = Agent(Raises(error), NO_DATA).ask("hi", [])
    assert reply.error and reply.problem.kind == kind and reply.text == message


def test_console_link_only_for_key_credits_and_limit():
    console = {kind for kind, error, _ in CASES if classify(error).console}
    assert console == {"auth", "credits", "limit"}


def test_retry_after_is_shown_when_anthropic_sends_it():
    error = status_error(anthropic.RateLimitError, 429, "rate limited", headers={"retry-after": "12"},
                         error_type="rate_limit_error")
    assert classify(error).text == "Too many requests at once. Give me a few seconds. (Anthropic says: try again in 12s.)"


def test_credits_and_limit_are_told_apart_from_an_ordinary_400_and_429():
    plain_400 = status_error(anthropic.BadRequestError, 400, "messages: roles must alternate")
    plain_429 = status_error(anthropic.RateLimitError, 429, "slow down", error_type="rate_limit_error")
    assert classify(plain_400).kind == "unknown" and classify(plain_429).kind == "rate"


def test_402_billing_error_counts_as_out_of_credits():
    assert classify(status_error(anthropic.APIStatusError, 402, "billing", error_type="billing_error")).kind == "credits"


def test_an_unknown_error_names_the_status_and_is_logged(caplog):
    error = status_error(anthropic.NotFoundError, 404, "model: claude-nope not found", error_type="not_found_error")
    with caplog.at_level(logging.ERROR, logger="progen.assistant"):
        reply = Agent(Raises(error), NO_DATA).ask("hi", [])
    assert reply.text == "Something went wrong talking to Anthropic (status 404)."
    assert "claude-nope" in caplog.text  # the full error is in the console


# ---- In the app ----

@pytest.fixture
def app_and_client(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: dt.datetime(2026, 10, 11, 10, 0))
    app = create_app(":memory:")
    client = TestClient(app, headers={"HX-Request": "true"})
    client.post("/ui/reading/series", data={"title": "Solo Leveling", "kind": "manhwa", "source": "x",
                                            "start_chapter": "119"})
    return app, client


def test_a_problem_is_a_warning_bubble_with_console_and_try_again(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = Raises(CASES[2][1])  # out of credit
    html = say(client, "how many chapters?")
    assert "sunny-problem" in html and "I&#39;m out of credits" in html
    assert 'href="https://console.anthropic.com"' in html and 'target="_blank"' in html and "Open console" in html
    assert "Try again" in html and 'data-sunny-retry="how many chapters?"' in html
    assert "how many chapters?" in client.get("/ui/assistant").text  # the message stays in the conversation


def test_rate_and_offline_have_no_console_link(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = Raises(connection_error())
    html = say(client, "hi")
    assert "Is the internet on?" in html and "Open console" not in html and "Try again" in html


def test_try_again_does_not_repeat_the_message_in_the_chat(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = Raises(connection_error())
    say(client, "hi")
    app.state.sunny.client = FakeClient(response(text("Hello, Progen.")))
    say(client, "hi", retry=True)
    log = client.get("/ui/assistant").text
    assert log.count('class="sunny-msg from-user">hi<') == 1 and "Hello, Progen." in log


def test_key_or_credit_problem_shows_when_opening_until_a_call_works(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = Raises(CASES[0][1])  # key expired
    say(client, "hi")
    opened = client.get("/ui/assistant").text
    assert "sunny-banner" in opened and "My key expired" in opened.split("sunny-banner", 1)[1]
    app.state.sunny.client = FakeClient(response(text("Back from the shadows.")))
    say(client, "hi again")
    assert "sunny-banner" not in client.get("/ui/assistant").text


def test_a_passing_problem_does_not_stick_when_opening(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = Raises(connection_error())
    say(client, "hi")
    assert "sunny-banner" not in client.get("/ui/assistant").text


def test_confirm_still_works_after_a_problem(app_and_client):
    app, client = app_and_client
    sid = client.get("/reading/library").json()[0]["series_id"]
    app.state.sunny.client = FakeClient(response(tool("set_reading_progress", {"series_id": sid, "chapter": 200})),
                                        response(text("Confirm it.")))
    card = say(client, "120")
    proposal = card.split('id="sunny-card-', 1)[1].split('"', 1)[0]
    app.state.sunny.client = Raises(CASES[2][1])  # now the credit has run out
    say(client, "and 121?")
    confirmed = client.post(f"/ui/assistant/confirm/{proposal}")  # local: no API needed
    assert confirmed.headers["HX-Trigger"] == "reading-changed"
    assert client.get("/reading/library").json()[0]["current_chapter"] == 200
