"""You choose Sunny's model in Settings. Stored as an event, like the planner reminders.
Fake client, never the real API."""
import datetime as dt
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from markupsafe import escape
from fakes import FakeClient, response, text
from fastapi.testclient import TestClient

import app.assistant.aggregate as aggregate
from app.assistant.agent import Agent
from app.assistant.aggregate import AssistantSettings
from app.assistant.commands import AssistantCommandHandler, ChangeAssistantModel
from app.assistant.events import AssistantSettingsChanged
from app.assistant.models import DEFAULT_MODEL, MODELS
from app.domain import DomainError
from app.eventstore import EventStore
from app.event_types import ALL_EVENT_TYPES
from app.main import create_app

APP = Path(__file__).resolve().parents[2] / "app"
NOW = SimpleNamespace(now=lambda: dt.datetime(2026, 10, 11, 10, 0))


# ---- The list ----

def test_three_models_with_name_description_and_price():
    assert [(m.id, m.name, m.price) for m in MODELS] == [
        ("claude-haiku-5-5", "Claude Haiku 5.5", "$0.10 / $0.50"),
        ("claude-sonnet-5-5", "Claude Sonnet 5.5", "$2 / $10"),
        ("claude-opus-5-5", "Claude Opus 5.5", "$4 / $20"),
    ]
    assert DEFAULT_MODEL == "claude-haiku-5-5" and all(m.description for m in MODELS)
    assert MODELS[0].short == "Haiku 5.5"


def test_no_model_ids_are_hardcoded_outside_models_py():
    found = []
    for path in APP.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".html", ".js", ".css", ".md"} and path.name != "models.py":
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"claude-[a-z]", line):
                    found.append(f"{path.relative_to(APP)}:{number}")
    assert found == []


# ---- Storage ----

def store_with(*events):
    store = EventStore(":memory:", ALL_EVENT_TYPES)
    if events:
        store.append(AssistantSettings.STREAM, list(events))
    return store


def test_without_an_event_it_is_haiku():
    assert AssistantSettings([]).model == "claude-haiku-5-5"


def test_change_the_model_and_it_stays_after_reloading(tmp_path):
    path = str(tmp_path / "tracker.db")
    store = EventStore(path, ALL_EVENT_TYPES)
    [event] = AssistantCommandHandler(store).handle(ChangeAssistantModel("claude-sonnet-5-5"))
    assert isinstance(event, AssistantSettingsChanged)
    reloaded = EventStore(path, ALL_EVENT_TYPES)
    assert AssistantSettings(reloaded.load_stream(AssistantSettings.STREAM)).model == "claude-sonnet-5-5"
    store.close()
    reloaded.close()


def test_the_same_model_again_gives_no_event():
    store = store_with(AssistantSettingsChanged("claude-sonnet-5-5"))
    assert AssistantCommandHandler(store).handle(ChangeAssistantModel("claude-sonnet-5-5")) == []
    assert AssistantCommandHandler(store).handle(ChangeAssistantModel(DEFAULT_MODEL))  # back to Haiku is allowed


def test_an_unknown_model_is_refused():
    with pytest.raises(DomainError):
        AssistantCommandHandler(store_with()).handle(ChangeAssistantModel("gpt-nonsense"))


def test_a_stored_model_that_left_the_list_falls_back_to_the_default(caplog, monkeypatch):
    monkeypatch.setattr(aggregate, "_warned_model", None)
    events = [AssistantSettingsChanged("an-old-model-that-was-removed")]
    with caplog.at_level(logging.WARNING, logger="progen.assistant"):
        for _ in range(3):  # rebuilt on every message
            assert AssistantSettings(events).model == DEFAULT_MODEL
    assert caplog.text.count("an-old-model-that-was-removed") == 1  # warned only once


# ---- Agent ----

def test_the_agent_sends_the_chosen_model_and_picks_up_a_change_at_once():
    store = store_with()
    settings = lambda: AssistantSettings(store.load_stream(AssistantSettings.STREAM))  # noqa: E731
    client = FakeClient(response(text("Haiku here.")), response(text("Sonnet here.")))
    agent = Agent(client, NOW, settings)
    agent.ask("first", [])
    AssistantCommandHandler(store).handle(ChangeAssistantModel("claude-sonnet-5-5"))
    agent.ask("second", [])
    assert [call["model"] for call in client.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert all(call["output_config"] == {"effort": "low"} and call["max_tokens"] == 1024 for call in client.calls)


# ---- Settings and the panel ----

@pytest.fixture
def client():
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def test_settings_has_a_sunny_panel_with_every_model_and_the_current_one_chosen(client):
    settings = client.get("/ui/settings").text
    assert 'hx-get="/ui/assistant/settings"' in settings and "Your assistant" in settings
    panel = client.get("/ui/assistant/settings").text
    for model in MODELS:
        assert f'value="{model.id}"' in panel and model.name in panel and model.price in panel
        assert str(escape(model.description)) in panel  # the way Jinja puts it in the HTML
    assert re.search(r'value="claude-haiku-5-5"\s+checked', panel)


def test_choosing_a_model_saves_it_and_toasts(client):
    reply = client.post("/ui/assistant/model", data={"model": "claude-sonnet-5-5"})
    assert "Sunny now uses Claude Sonnet 5.5." in reply.text and "assistant-changed" in reply.headers["HX-Trigger"]
    assert re.search(r'value="claude-sonnet-5-5"\s+checked', client.get("/ui/assistant/settings").text)
    assert client.get("/ui/assistant/model-label").text.strip() == "Sonnet 5.5"
    assert "toast-error" in client.post("/ui/assistant/model", data={"model": "gpt-nonsense"}).text


def test_the_model_change_goes_through_the_csrf_check():
    plain = TestClient(create_app(":memory:"))  # without HX-Request: like another website
    assert plain.post("/ui/assistant/model", data={"model": "claude-opus-5-5"}).status_code == 403


def test_key_status_without_showing_the_key(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    panel = client.get("/ui/assistant/settings").text
    assert "API key found" in panel and "sk-ant-secret-value" not in panel
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "No API key in .env" in client.get("/ui/assistant/settings").text


def test_the_chat_panel_shows_the_model_name_under_its_title(client):
    shell = client.get("/").text
    assert 'id="sunny-model"' in shell and 'hx-get="/ui/assistant/model-label"' in shell
    assert "assistant-changed from:body" in shell
    assert client.get("/ui/assistant/model-label").text.strip() == "Haiku 5.5"


def test_the_choice_is_in_a_backup(tmp_path):
    from app.backup import Backups
    store = store_with(AssistantSettingsChanged("claude-opus-5-5"))
    info = Backups(store, tmp_path / "covers", tmp_path / "backups").create()
    copy = EventStore(str(info.path / "tracker.db"), ALL_EVENT_TYPES)
    assert AssistantSettings(copy.load_stream(AssistantSettings.STREAM)).model == "claude-opus-5-5"
    copy.close()
