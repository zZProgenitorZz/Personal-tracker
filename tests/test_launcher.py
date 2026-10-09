"""De desktop-launcher (launch.pyw, stop.pyw) en de afsluitroute van de server."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.shutdown import SHUTDOWN_HEADER

ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    """launch.pyw en stop.pyw zijn scripts (.pyw), geen modules: zo laden we ze toch."""
    path = ROOT / name
    loader = SourceFileLoader(name.replace(".", "_"), str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


# ---- Afsluitroute ----

@pytest.fixture
def stops():
    return []


def client_from(host: str, stops):
    app = create_app(":memory:")
    app.state.request_shutdown = lambda: stops.append("stop")
    return TestClient(app, client=(host, 50000))


def test_shutdown_from_this_computer_with_header(stops):
    response = client_from("127.0.0.1", stops).post("/admin/shutdown", headers={SHUTDOWN_HEADER: "1"})
    assert response.status_code == 202 and stops == ["stop"]


def test_shutdown_needs_the_header(stops):
    # Een website in je browser kan wel naar 127.0.0.1 posten, maar geen eigen header meesturen.
    assert client_from("127.0.0.1", stops).post("/admin/shutdown").status_code == 403
    assert stops == []


@pytest.mark.parametrize("host", ["192.168.1.20", "testclient"])
def test_shutdown_only_from_this_computer(stops, host):
    response = client_from(host, stops).post("/admin/shutdown", headers={SHUTDOWN_HEADER: "1"})
    assert response.status_code == 403 and stops == []


def test_shutdown_is_not_in_the_api_docs():
    assert "/admin/shutdown" not in TestClient(create_app(":memory:")).get("/openapi.json").text


# ---- launch.pyw ----

def test_server_always_on_localhost_without_reload():
    launch = load("launch.pyw")
    command = launch.server_command()
    assert command[1:4] == ["-m", "uvicorn", "app.main:create_app"]
    assert "--factory" in command and "--reload" not in command
    assert command[command.index("--host") + 1] == "127.0.0.1" and "0.0.0.0" not in command
    assert command[command.index("--port") + 1] == "8000"


def test_uses_the_venv_python():
    launch = load("launch.pyw")
    assert launch.python_executable() == ROOT / ".venv" / "Scripts" / "python.exe"


@pytest.fixture
def launch(monkeypatch):
    """launch.pyw met nep-onderdelen: geen echte server, geen venster, geen icoon."""
    module = load("launch.pyw")
    calls = {"started": 0, "windows": 0, "trays": 0, "messages": []}
    monkeypatch.setattr(module, "claim_tray", lambda: True)
    monkeypatch.setattr(module, "start_server", lambda: calls.__setitem__("started", calls["started"] + 1) or FakeProcess())
    monkeypatch.setattr(module, "open_window", lambda: calls.__setitem__("windows", calls["windows"] + 1))
    monkeypatch.setattr(module, "run_tray", lambda: calls.__setitem__("trays", calls["trays"] + 1))
    monkeypatch.setattr(module, "show_message", lambda text, **kw: calls["messages"].append(text))
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    module.calls = calls
    return module


def test_skips_starting_when_progen_already_runs(launch, monkeypatch):
    monkeypatch.setattr(launch, "is_running", lambda: True)
    launch.main([])
    assert launch.calls["started"] == 0 and launch.calls["windows"] == 1 and launch.calls["trays"] == 1


def test_starts_server_waits_opens_window_and_shows_the_icon(launch, monkeypatch):
    answers = iter([False, False, True])  # eerst niet, na even wachten wel
    monkeypatch.setattr(launch, "is_running", lambda: next(answers))
    launch.main([])
    assert (launch.calls["started"], launch.calls["windows"], launch.calls["trays"]) == (1, 1, 1)


def test_background_start_opens_no_window(launch, monkeypatch):
    answers = iter([False, True])
    monkeypatch.setattr(launch, "is_running", lambda: next(answers))
    launch.main(["--background"])
    assert (launch.calls["started"], launch.calls["windows"], launch.calls["trays"]) == (1, 0, 1)


def test_second_click_only_opens_a_window(launch, monkeypatch):
    # Er is al een icoon (en dus een launcher die de server beheert).
    monkeypatch.setattr(launch, "claim_tray", lambda: False)
    monkeypatch.setattr(launch, "is_running", lambda: True)
    launch.main([])
    assert (launch.calls["started"], launch.calls["windows"], launch.calls["trays"]) == (0, 1, 0)


def test_tells_you_when_the_server_does_not_start(launch, monkeypatch):
    monkeypatch.setattr(launch, "is_running", lambda: False)
    monkeypatch.setattr(launch, "start_server", lambda: FakeProcess(exit_code=1))
    launch.main([])
    assert launch.calls["windows"] == 0 and launch.calls["trays"] == 0
    assert "launcher.log" in launch.calls["messages"][0]


def test_falls_back_to_default_browser_without_edge(monkeypatch):
    launch = load("launch.pyw")
    opened = []
    monkeypatch.setattr(launch, "find_edge", lambda: None)
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: opened.append(url))
    launch.open_window()
    assert opened == ["http://127.0.0.1:8000"]


def test_edge_opens_as_its_own_app_window(monkeypatch):
    launch = load("launch.pyw")
    commands = []
    monkeypatch.setattr(launch, "find_edge", lambda: "msedge.exe")
    monkeypatch.setattr(launch.subprocess, "Popen", lambda command, **kw: commands.append(command))
    launch.open_window()
    assert commands == [["msedge.exe", "--app=http://127.0.0.1:8000"]]


class FakeProcess:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


# ---- Het icoon in het systeemvak ----

class FakeIcon:
    def __init__(self):
        self.stopped = False
        self.notes = []

    def stop(self):
        self.stopped = True

    def notify(self, text, title):
        self.notes.append(text)


def test_tray_menu(monkeypatch):
    launch = load("launch.pyw")
    icon, opened = FakeIcon(), []
    monkeypatch.setattr(launch, "open_window", lambda: opened.append(1))
    monkeypatch.setattr(launch, "sync_spotify", lambda: "Synced with Spotify · 3 new plays")
    monkeypatch.setattr(launch, "request_stop", lambda: None)
    actions = launch.tray_menu_actions(icon)
    assert [name for name, _ in actions] == ["Open Progen", "Sync Spotify now", "Stop Progen"]
    for _, action in actions:
        action()
    assert opened == [1] and icon.notes == ["Synced with Spotify · 3 new plays"] and icon.stopped


def test_stop_in_the_tray_keeps_the_icon_when_stopping_fails(monkeypatch):
    launch = load("launch.pyw")
    icon, messages = FakeIcon(), []
    monkeypatch.setattr(launch, "request_stop", lambda: "Progen refused to stop (HTTP 403).")
    monkeypatch.setattr(launch, "show_message", lambda text, **kw: messages.append(text))
    dict(launch.tray_menu_actions(icon))["Stop Progen"]()
    assert not icon.stopped and messages


def test_icon_disappears_when_the_server_is_gone(monkeypatch):
    import threading
    launch = load("launch.pyw")
    monkeypatch.setattr(launch, "WATCH_INTERVAL", 0.01)
    answers = iter([True, True, False])
    monkeypatch.setattr(launch, "is_running", lambda: next(answers))
    icon = FakeIcon()
    launch.watch_server(icon, threading.Event())
    assert icon.stopped


def test_tray_icon_image_comes_from_the_ico():
    from PIL import Image
    with Image.open(ROOT / "app" / "static" / "icon.ico") as ico:
        assert (64, 64) in ico.info["sizes"]


# ---- stop.pyw en app/desktop.py ----

class Answer:
    def __init__(self, body=b""):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_stop_sends_the_header_to_localhost(monkeypatch):
    from app import desktop
    sent = []
    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda request, timeout: sent.append(request) or Answer())
    monkeypatch.setattr(desktop, "is_running", lambda: False)
    stop = load("stop.pyw")
    messages = []
    monkeypatch.setattr(stop, "show_message", lambda text, **kw: messages.append(text))
    stop.main()
    [request] = sent
    assert request.full_url == "http://127.0.0.1:8000/admin/shutdown" and request.get_method() == "POST"
    assert request.get_header(SHUTDOWN_HEADER.capitalize()) == "1" and messages == []


def test_stop_when_progen_is_not_running(monkeypatch):
    from app import desktop

    def refused(request, timeout):
        raise desktop.urllib.error.URLError("connection refused")

    monkeypatch.setattr(desktop.urllib.request, "urlopen", refused)
    stop = load("stop.pyw")
    messages = []
    monkeypatch.setattr(stop, "show_message", lambda text, **kw: messages.append(text))
    stop.main()
    assert messages == ["Progen isn't running."]


def test_sync_from_the_tray_returns_the_message_as_text(monkeypatch):
    from app import desktop
    html = '<div class="toast"><span class="toast-icon">x</span><span>Synced with Spotify &#39;now&#39; · 2 new plays</span></div>'.encode()
    sent = []
    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda request, timeout: sent.append(request) or Answer(html))
    assert desktop.sync_spotify().endswith("Synced with Spotify 'now' · 2 new plays")
    assert sent[0].get_header("Hx-request") == "true"
