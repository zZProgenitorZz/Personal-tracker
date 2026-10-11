"""Sunny's conversation (session.py): a robust Confirm, and no lock during the API call. Fake clients, no API."""
import datetime as dt
import threading

import pytest
from fakes import FakeClient, response, text
from fastapi.testclient import TestClient

import app.clock as clock
from app.assistant.session import Proposal
from app.assistant.tools import PendingAction
from app.main import create_app
from app.reading.commands import LogProgress
from app.watching.commands import ChangeShowStatus
from app.watching.events import WatchStatus


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: dt.datetime(2026, 10, 11, 10, 0))
    app = create_app(":memory:")
    client = TestClient(app, headers={"HX-Request": "true"})
    client.post("/ui/reading/series", data={"title": "Solo Leveling", "kind": "manhwa", "source": "x",
                                            "start_chapter": "119"})
    app.test_client = client
    return app


def solo(app):
    return app.state.sunny.data.library.find_by_title("Solo Leveling")


# ---- Confirm: an unexpected error ----

def test_an_unexpected_error_fails_the_proposal_and_a_second_confirm_does_nothing(app, monkeypatch):
    sunny = app.state.sunny
    calls = []

    def explode(command):
        calls.append(command)
        raise RuntimeError("database on fire")

    monkeypatch.setattr(sunny.data.watching, "handle", explode)
    sunny.proposals["p1"] = Proposal("p1", [
        PendingAction("reading", LogProgress(solo(app).series_id, 120), "Solo Leveling · chapter 119 → 120",
                      "reading-changed"),
        PendingAction("watching", ChangeShowStatus("x", WatchStatus.DROPPED), "Severance · Watching → Dropped",
                      "watching-changed"),
    ])
    result = sunny.confirm("p1")
    assert result.proposal.state == "failed" and "unexpected error" in result.proposal.problem
    assert result.changed == ["reading-changed"] and result.toast["error"]
    assert solo(app).current_chapter == 120 and len(calls) == 1

    again = sunny.confirm("p1")  # never run twice
    assert again.changed == [] and again.note is None and len(calls) == 1 and solo(app).current_chapter == 120


def test_the_web_route_still_answers_after_an_unexpected_error(app, monkeypatch):
    sunny = app.state.sunny
    monkeypatch.setattr(sunny.data.reading, "handle", lambda command: (_ for _ in ()).throw(RuntimeError("boom")))
    sunny.proposals["p2"] = Proposal("p2", [PendingAction("reading", LogProgress(solo(app).series_id, 120),
                                                          "Solo Leveling · chapter 119 → 120", "reading-changed")])
    reply = app.test_client.post("/ui/assistant/confirm/p2")
    assert reply.status_code == 200 and "toast-error" in reply.text and "HX-Trigger" not in reply.headers


# ---- No lock during the API call ----

class SlowClient:
    """Keeps "thinking" until the test lets it go."""

    def __init__(self):
        self.messages = self
        self.thinking = threading.Event()
        self.release = threading.Event()

    def create(self, **request):
        self.thinking.set()
        assert self.release.wait(5), "the test never released the slow client"
        return response(text("Took my time."))


def test_confirm_cancel_and_new_chat_work_while_sunny_thinks(app):
    sunny = app.state.sunny
    sunny.proposals["p3"] = Proposal("p3", [PendingAction("reading", LogProgress(solo(app).series_id, 121),
                                                          "Solo Leveling · chapter 119 → 121", "reading-changed")])
    sunny.proposals["p4"] = Proposal("p4", [PendingAction("reading", LogProgress(solo(app).series_id, 130),
                                                          "Solo Leveling · chapter 121 → 130", "reading-changed")])
    slow = SlowClient()
    sunny.client = slow
    asking = threading.Thread(target=sunny.send, args=("Where am I?", False))
    asking.start()
    assert slow.thinking.wait(5)

    done = {}
    worker = threading.Thread(target=lambda: done.update(confirm=sunny.confirm("p3"), cancel=sunny.cancel("p4")))
    worker.start()
    worker.join(2)
    assert not worker.is_alive(), "Confirm/Cancel waited for the API call"
    assert done["confirm"].proposal.state == "confirmed" and done["cancel"].proposal.state == "cancelled"
    assert solo(app).current_chapter == 121

    resetting = threading.Thread(target=sunny.reset)
    resetting.start()
    resetting.join(2)
    assert not resetting.is_alive(), "New chat waited for the API call"

    slow.release.set()
    asking.join(5)
    # The answer simply ends up in the new conversation.
    assert [e["text"] for e in sunny.entries if e["kind"] == "sunny"] == ["Took my time."]


def test_send_returns_the_new_entries(app):
    sunny = app.state.sunny
    sunny.client = FakeClient(response(text("Chapter 119.")))
    new = sunny.send("Where am I in Solo Leveling?", retry=False)
    assert [e["kind"] for e in new] == ["sunny"] and new[0]["text"] == "Chapter 119."
    assert sunny.entries[0] == {"kind": "user", "text": "Where am I in Solo Leveling?"}
