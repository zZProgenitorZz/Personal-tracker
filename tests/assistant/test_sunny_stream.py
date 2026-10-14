"""Sunny's answer as it comes: the agent's stream, the NDJSON route and the panel. Fake client, no API."""
import datetime as dt
import pathlib

import anthropic
import httpx2
import pytest
from fakes import FakeClient, failing, response, say, stream_lines, text, tool
from fastapi.testclient import TestClient

import app.clock as clock
from app.assistant.agent import Agent, Reply
from app.main import create_app


@pytest.fixture
def app_and_client(monkeypatch):
    monkeypatch.setattr(clock, "local_now", lambda: dt.datetime(2026, 10, 11, 10, 0))
    app = create_app(":memory:")
    client = TestClient(app, headers={"HX-Request": "true"})
    client.post("/ui/reading/series", data={"title": "Solo Leveling", "kind": "manhwa", "source": "x",
                                            "start_chapter": "119"})
    return app, client


def solo_id(client):
    return client.get("/reading/library").json()[0]["series_id"]


def offline():
    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))


# ---- The agent ----

def test_ask_stream_gives_text_in_order_a_status_per_tool_and_one_reply(app_and_client):
    app, _ = app_and_client
    client = FakeClient(response(text("Let me check."), tool("history_between", {"from": "2026-09-01",
                                                                                "to": "2026-09-30", "domain": None})),
                        response(text("Nothing read in September.")))
    events = list(Agent(client, app.state.sunny.data).ask_stream("September?", []))
    kinds = [kind for kind, _ in events]
    assert kinds[-1] == "reply" and kinds.count("reply") == 1
    assert ("status", "Looking through your history…") in events
    streamed = "".join(value for kind, value in events if kind == "text")
    assert streamed == "Let me check.\nNothing read in September."
    reply = events[-1][1]
    assert isinstance(reply, Reply) and reply.text == streamed and not reply.error  # what you saw is what stays
    assert kinds.index("status") < len(kinds) - 2  # the status comes before the second round's text


def test_an_unknown_tool_gets_the_default_status(app_and_client):
    app, _ = app_and_client
    client = FakeClient(response(tool("plan_skip", {"plan_id": "nope", "on": "2026-10-12"})), response(text("No.")))
    events = list(Agent(client, app.state.sunny.data).ask_stream("skip it", []))
    assert ("status", "Looking that up…") in events


def test_an_error_after_some_text_ends_in_the_problem(app_and_client):
    app, _ = app_and_client
    client = FakeClient(failing(offline(), text("Half an ans")))
    events = list(Agent(client, app.state.sunny.data).ask_stream("hi", []))
    assert [kind for kind, _ in events][:1] == ["text"]
    reply = events[-1][1]
    assert reply.problem.kind == "offline" and reply.text == "I can't reach Anthropic. Is the internet on?"


# ---- The route ----

def test_the_stream_route_sends_text_lines_and_last_the_rendered_answer(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = FakeClient(response(text("Chapter 119. You're welcome, Progen.")))
    lines = stream_lines(client, "Where am I?")
    assert [line["t"] for line in lines[:-1]] == ["text"] * (len(lines) - 1) and len(lines) > 2
    assert "".join(line["v"] for line in lines[:-1]) == "Chapter 119. You're welcome, Progen."
    assert lines[-1]["t"] == "done" and "sunny-bubble" in lines[-1]["html"] and "Chapter 119" in lines[-1]["html"]
    assert "Where am I?" in client.get("/ui/assistant").text  # kept like before


def test_a_proposal_card_is_in_the_done_html(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = FakeClient(response(tool("set_reading_progress", {"series_id": solo_id(client),
                                                                                "chapter": 200})),
                                        response(text("Confirm it.")))
    lines = stream_lines(client, "read 120")
    assert {"t": "status", "v": "Looking that up…"} in lines
    html = lines[-1]["html"]
    assert "Solo Leveling · chapter 119 → 200" in html and 'hx-post="/ui/assistant/confirm/' in html


def test_an_api_error_ends_in_the_problem_balloon_without_the_half_answer(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = FakeClient(failing(offline(), text("Half an answer")))
    html = say(client, "hi")
    assert "sunny-problem" in html and "Is the internet on?" in html and "Half an answer" not in html
    assert 'data-sunny-retry="hi"' in html


def test_model_text_is_escaped_in_the_done_html(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = FakeClient(response(text("<script>alert(1)</script> done")))
    html = say(client, "hi")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_without_a_key_one_done_line(app_and_client):
    _, client = app_and_client
    lines = stream_lines(client, "hi")
    assert len(lines) == 1 and "ANTHROPIC_API_KEY" in lines[0]["html"]


def test_the_stream_is_a_change_so_other_websites_are_refused(app_and_client):
    app, _ = app_and_client
    stranger = TestClient(app)  # no HX-Request header, like another website
    assert stranger.post("/ui/assistant/stream", data={"message": "hi"}).status_code == 403
    assert TestClient(app, headers={"HX-Request": "true"}).post("/ui/assistant/message",
                                                               data={"message": "hi"}).status_code in (404, 405)


# ---- The panel ----

def test_the_panel_sends_itself_and_shows_that_sunny_is_busy(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = FakeClient()
    body = client.get("/ui/assistant").text
    form = body.split('id="sunny-form"', 1)[1].split(">", 1)[0]
    assert "hx-post" not in form and "sunny-typing" not in body
    thinking = body.split('id="sunny-thinking"', 1)[1].split("</template>", 1)[0]
    assert 'aria-live="polite"' in thinking and "sunny-dots" in thinking and "Sunny is lurking in the shadows…" in thinking
    assert '<svg class="icon"' in thinking  # the flame
    assert 'id="sunny-offline"' in body and "Is the internet on?" in body
    js = client.get("/static/trackly.js").text
    assert "/ui/assistant/stream" in js and '"HX-Request": "true"' in js and ".textContent +=" in js
    css = client.get("/static/trackly.css").text
    assert "sunny-flicker" in css and "prefers-reduced-motion" in css and ".sunny-typing" not in css


def test_hold_to_talk_is_a_hidden_mic_that_sends_through_the_same_form(app_and_client):
    app, client = app_and_client
    app.state.sunny.client = FakeClient()
    body = client.get("/ui/assistant").text
    form = body.split('id="sunny-form"', 1)[1].split("</form>", 1)[0]
    mic = form.split('class="btn btn-ghost btn-sm sunny-mic"', 1)[1].split("</button>", 1)[0]
    assert form.index("sunny-input") < form.index("sunny-mic") < form.index("sunny-send")  # between field and Send
    assert 'type="button"' in mic and 'aria-label="Hold to talk"' in mic and 'aria-pressed="false"' in mic
    assert " hidden>" in mic and '<svg class="icon"' in mic  # only shown when the browser can listen
    js = client.get("/static/trackly.js").text
    assert "window.SpeechRecognition || window.webkitSpeechRecognition" in js
    talk = js.split("// ---- Sunny: hold to talk ----", 1)[1].split("// ---- Sunny: notices", 1)[0]
    assert 'el("sunny-form").requestSubmit()' in talk and "fetch(" not in talk  # no second way to send
    assert "setPointerCapture" in talk and "MIC_MIN_MS = 300" in talk and "alert(" not in talk
    css = client.get("/static/trackly.css").text
    assert "touch-action: none" in css and "-webkit-touch-callout: none" in css and ".sunny-form.is-recording" in css
    identity = (pathlib.Path("app/assistant/identity/IDENTITY.md")).read_text(encoding="utf-8")
    assert "Exception: hold-to-talk uses the browser's own speech recognition" in identity


# ---- Talking back ----

def test_a_spoken_message_reaches_the_model_marked_as_voice_and_the_panel_shows_it_plain(app_and_client):
    app, client = app_and_client
    fake = FakeClient(response(text("Chapter one hundred and nineteen.")))
    app.state.sunny.client = fake
    reply = client.post("/ui/assistant/stream", data={"message": "where am I in Solo Leveling", "voice": "1"})
    assert reply.status_code == 200
    assert fake.calls[-1]["messages"][-1] == {"role": "user", "content": "(voice) where am I in Solo Leveling"}
    panel = client.get("/ui/assistant").text
    assert "where am I in Solo Leveling" in panel and "(voice)" not in panel
    assert app.state.sunny.entries[0] == {"kind": "user", "text": "where am I in Solo Leveling"}


def test_without_voice_nothing_changes(app_and_client):
    app, client = app_and_client
    fake = FakeClient(response(text("Chapter 119.")))
    app.state.sunny.client = fake
    say(client, "where am I in Solo Leveling")
    assert fake.calls[-1]["messages"][-1] == {"role": "user", "content": "where am I in Solo Leveling"}


def test_sunny_knows_how_to_answer_out_loud():
    soul = pathlib.Path("app/assistant/identity/SOUL.md").read_text(encoding="utf-8")
    assert soul.index("## When Progen doesn't get it") < soul.index("## When you're spoken to") < soul.index("## Frugal")
    assert 'A message starting with "(voice)" was spoken' in soul and 'end with "Say yes or tap Confirm."' in soul


def test_the_voice_settings_and_the_talking_back_block_are_there(app_and_client):
    app, client = app_and_client
    settings = client.get("/ui/assistant/settings").text
    assert 'id="sunny-voices"' in settings and 'data-voice-language="nl"' in settings and "data-voice-test" in settings
    js = client.get("/static/trackly.js").text
    talk = js.split("// ---- Sunny: talking back ----", 1)[1].split("// ---- Sunny: wake word", 1)[0]
    assert "speechSynthesis" in talk and "voiceschanged" in talk and "localStorage" in talk
    assert "requestSubmit" not in talk and "fetch(" not in talk  # no second way to send
    assert "Deleting needs a click." in talk and '"sunny-delete"' in talk


def test_one_voice_for_both_languages_and_the_wake_word_switch(app_and_client):
    _, client = app_and_client
    settings = client.get("/ui/assistant/settings").text
    row = settings.split('id="sunny-wake-row"', 1)[1].split("</label>", 1)[0]
    assert " hidden>" in row and 'Listen for "Hey Sunny"' in row and 'id="sunny-wake"' in row
    assert "Microsoft&#39;s speech service the whole time" in row or "Microsoft's speech service the whole time" in row
    js = client.get("/static/trackly.js").text
    assert '"Same as English voice", VOICE_SAME' in js and "VOICE_MULTILINGUAL = /multilingual/i" in js
    wake = js.split("// ---- Sunny: wake word ----", 1)[1].split("// ---- Sunny: notices", 1)[0]
    assert '"sunless"' in wake and '"hey"' in wake and "function wakeUpdate()" in wake
    assert "requestSubmit()" in wake and "fetch(" not in wake  # the same way to send as hands-free
    assert '"visibilitychange"' in wake and "voiceSpeaking" in wake and 'startsWith("#settings")' in wake
    assert "is-wake-listening" in wake and "localStorage" not in wake  # stored through voiceSave
    css = client.get("/static/trackly.css").text
    assert ".sunny-fab.is-wake-listening::after" in css


def test_the_answer_is_read_while_it_streams_and_the_listening_time_is_a_setting(app_and_client):
    _, client = app_and_client
    settings = client.get("/ui/assistant/settings").text
    listen = settings.split('id="sunny-listen"', 1)[1].split("</select>", 1)[0]
    assert '<option value="0">Don&#39;t</option>' in listen or "<option value=\"0\">Don't</option>" in listen
    assert '<option value="3000" selected>3 seconds</option>' in listen and '<option value="5000">5 seconds</option>' in listen
    js = client.get("/static/trackly.js").text
    ask = js.split("async function askSunny", 1)[1].split("function sunnyLine", 1)[0]
    assert "voiceQueueStart()" in ask and 'voice && message.t === "text") voiceAppend(message.v)' in ask
    talk = js.split("// ---- Sunny: talking back ----", 1)[1].split("// ---- Sunny: wake word", 1)[0]
    assert "VOICE_LISTEN_DEFAULT = 3000" in talk and "VOICE_SILENCE_MS" not in js
    assert "function voiceAppend(" in talk and "function voiceFlush(" in talk and "SPEECH?.cancel()" in talk
    assert 'micHint("Conversation ended", 2000)' in js
