"""Sunny in the app: the button and the panel, messages, Confirm/Cancel and New chat. Fake client, no API."""
import datetime as dt

import pytest
from fakes import FakeClient, response, text, tool
from fastapi.testclient import TestClient

import app.clock as clock
from app.assistant.agent import make_client
from app.main import create_app


@pytest.fixture
def app_and_client(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: dt.datetime(2026, 10, 11, 10, 0))
    app = create_app(":memory:")
    client = TestClient(app, headers={"HX-Request": "true"})
    client.post("/ui/reading/series", data={"title": "Solo Leveling", "kind": "manhwa", "source": "asura",
                                            "start_chapter": "119"})
    return app, client


def give_sunny(app, *answers):
    fake = FakeClient(*answers)
    app.state.sunny.client = fake
    return fake


def solo_id(client):
    return next(s["series_id"] for s in client.get("/reading/library").json() if s["title"] == "Solo Leveling")


def card_id(html):
    return html.split('id="sunny-card-', 1)[1].split('"', 1)[0]


# ---- Button and panel ----

def test_button_and_panel_live_in_the_shell_and_hide_on_settings(app_and_client):
    _, client = app_and_client
    shell = client.get("/").text
    assert 'id="sunny-fab"' in shell and 'id="sunny-panel"' in shell and 'hx-get="/ui/assistant"' in shell
    js = client.get("/static/trackly.js").text
    assert 'route.section === "settings"' in js and "sunny" in js.split('route.section === "settings"')[1][:200]
    assert '"Escape"' in js


def test_without_a_key_there_is_one_tidy_line(app_and_client):
    _, client = app_and_client  # in the tests there is never a real client
    body = client.get("/ui/assistant").text
    assert "Add ANTHROPIC_API_KEY to .env to summon Sunny from the shadows" in body and "<form" not in body
    reply = client.post("/ui/assistant/message", data={"message": "hi"})
    assert reply.status_code == 200 and "ANTHROPIC_API_KEY" in reply.text


def test_make_client_only_with_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert make_client() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # only create it, never call it
    assert make_client() is not None


# ---- Messages ----

def test_a_question_gets_a_bubble_and_the_chat_stays(app_and_client):
    app, client = app_and_client
    give_sunny(app, response(text("Chapter 119. You're welcome, Progen.")))
    reply = client.post("/ui/assistant/message", data={"message": "Where am I in Solo Leveling?"}).text
    assert "sunny-bubble" in reply and "Chapter 119" in reply
    log = client.get("/ui/assistant").text  # e.g. after another page: the conversation is still there
    assert "Where am I in Solo Leveling?" in log and "Chapter 119" in log and "<form" in log


def test_a_proposal_is_a_card_and_nothing_is_saved_before_confirm(app_and_client):
    app, client = app_and_client
    sid = solo_id(client)
    give_sunny(app, response(tool("set_reading_progress", {"series_id": sid, "chapter": 120})),
               response(text("Chapter 120. Confirm it.")))
    reply = client.post("/ui/assistant/message", data={"message": "read 120 of solo leveling"}).text
    assert "Solo Leveling · chapter 119 → 120" in reply and "Confirm" in reply and "Cancel" in reply
    assert client.get("/reading/library").json()[0]["current_chapter"] == 119

    confirmed = client.post(f"/ui/assistant/confirm/{card_id(reply)}")
    assert confirmed.headers["HX-Trigger"] == "reading-changed"
    assert "toast" in confirmed.text and "hx-swap-oob" in confirmed.text
    assert client.get("/reading/library").json()[0]["current_chapter"] == 120
    again = client.post(f"/ui/assistant/confirm/{card_id(reply)}")  # not twice
    assert "HX-Trigger" not in again.headers


def test_cancel_saves_nothing(app_and_client):
    app, client = app_and_client
    give_sunny(app, response(tool("set_reading_progress", {"series_id": solo_id(client), "chapter": 200})),
               response(text("200? Bold.")))
    reply = client.post("/ui/assistant/message", data={"message": "read 200"}).text
    cancelled = client.post(f"/ui/assistant/cancel/{card_id(reply)}")
    assert "HX-Trigger" not in cancelled.headers and "What should be different?" in cancelled.text
    assert client.get("/reading/library").json()[0]["current_chapter"] == 119


def test_one_message_can_propose_several_actions_from_different_trackers(app_and_client):
    app, client = app_and_client
    give_sunny(app, response(tool("set_reading_progress", {"series_id": solo_id(client), "chapter": 120}, "a"),
                             tool("add_plan", {"text": "elke ma 12:00 gym"}, "b")),
               response(text("Two things. Confirm.")))
    reply = client.post("/ui/assistant/message", data={"message": "120 and gym every monday"}).text
    assert "New plan · gym · from Mon 12 Oct · 12:00 · every Mon" in reply
    confirmed = client.post(f"/ui/assistant/confirm/{card_id(reply)}")
    assert set(confirmed.headers["HX-Trigger"].split(", ")) == {"reading-changed", "planner-changed"}
    assert [p["title"] for p in client.get("/planner/plans").json()] == ["gym"]


def test_delete_asks_once_more_with_the_name(app_and_client):
    app, client = app_and_client
    give_sunny(app, response(tool("delete_item", {"domain": "reading", "id": solo_id(client)})),
               response(text("Gone, if you say so.")))
    reply = client.post("/ui/assistant/message", data={"message": "delete solo leveling"}).text
    assert "Delete · Solo Leveling" in reply
    assert 'hx-confirm="Solo Leveling"' in reply and 'data-confirm-kind="sunny-delete"' in reply
    assert "sunny-delete" in client.get("/static/trackly.js").text
    client.post(f"/ui/assistant/confirm/{card_id(reply)}")
    assert client.get("/reading/library").json() == []


def test_a_failed_action_is_reported_not_crashed(app_and_client):
    app, client = app_and_client
    give_sunny(app, response(tool("set_reading_progress", {"series_id": solo_id(client), "chapter": 120})),
               response(text("ok")))
    reply = client.post("/ui/assistant/message", data={"message": "120"}).text
    client.delete(f"/ui/reading/series/{solo_id(client)}")  # gone in the meantime
    failed = client.post(f"/ui/assistant/confirm/{card_id(reply)}")
    assert failed.status_code == 200 and "toast-error" in failed.text


def test_new_chat_clears_everything(app_and_client):
    app, client = app_and_client
    give_sunny(app, response(text("Hi.")))
    client.post("/ui/assistant/message", data={"message": "hello"})
    client.post("/ui/assistant/reset")
    assert "hello" not in client.get("/ui/assistant").text
    assert app.state.sunny.history == []


def test_the_history_sent_along_is_text_only_and_mentions_confirms(app_and_client):
    app, client = app_and_client
    fake = give_sunny(app, response(tool("set_reading_progress", {"series_id": solo_id(client), "chapter": 120})),
                      response(text("Confirm it.")), response(text("And 121 next time.")))
    reply = client.post("/ui/assistant/message", data={"message": "120"}).text
    client.post(f"/ui/assistant/confirm/{card_id(reply)}")
    client.post("/ui/assistant/message", data={"message": "and the next one too"})
    sent = fake.calls[-1]["messages"]
    assert all(isinstance(m["content"], str) for m in sent[:-1])  # no thinking blocks or tool output from earlier turns
    assert any("Confirm" in m["content"] and "chapter 119 → 120" in m["content"] for m in sent)
