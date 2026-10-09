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


def test_server_never_waits_forever_when_stopping():
    # Zonder grens kan uvicorn eeuwig wachten op een weggevallen verbinding van het venster.
    command = load("launch.pyw").server_command()
    assert int(command[command.index("--timeout-graceful-shutdown") + 1]) <= 10


def test_uses_the_venv_python():
    launch = load("launch.pyw")
    assert launch.python_executable() == ROOT / ".venv" / "Scripts" / "python.exe"


@pytest.fixture
def launch(monkeypatch):
    """launch.pyw met nep-onderdelen: geen echte server, geen venster, geen icoon."""
    module = load("launch.pyw")
    calls = {"started": 0, "windows": 0, "trays": 0, "messages": [], "dpi_aware": 0}
    monkeypatch.setattr(module, "make_dpi_aware", lambda: calls.__setitem__("dpi_aware", calls["dpi_aware"] + 1))
    monkeypatch.setattr(module, "claim_tray", lambda: True)
    monkeypatch.setattr(module, "start_server", lambda: calls.__setitem__("started", calls["started"] + 1) or FakeProcess())
    monkeypatch.setattr(module, "open_window", lambda: calls.__setitem__("windows", calls["windows"] + 1))
    monkeypatch.setattr(module, "run_tray", lambda: calls.__setitem__("trays", calls["trays"] + 1))
    monkeypatch.setattr(module, "show_message", lambda text, **kw: calls["messages"].append(text))
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    module.calls = calls
    return module


def test_launcher_is_dpi_aware_so_the_tray_menu_is_sharp(launch, monkeypatch):
    monkeypatch.setattr(launch, "is_running", lambda: True)
    launch.main(["--background"])
    assert launch.calls["dpi_aware"] == 1


def test_dpi_awareness_uses_per_monitor_v2_and_never_fails(monkeypatch):
    from app import desktop
    calls = []

    class User32:
        def SetProcessDpiAwarenessContext(self, context):
            calls.append(context.value)
            return 1

    class Windll:
        user32 = User32()

    monkeypatch.setattr(desktop.ctypes, "windll", Windll(), raising=False)
    desktop.make_dpi_aware()
    assert calls == [desktop.ctypes.c_void_p(desktop.PER_MONITOR_AWARE_V2).value]

    class Nothing:  # geen Windows, of een heel oude versie: gewoon doorgaan
        pass

    monkeypatch.setattr(desktop.ctypes, "windll", Nothing(), raising=False)
    desktop.make_dpi_aware()


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


def test_starts_the_server_even_when_an_old_icon_still_exists(launch, monkeypatch):
    # Een vastgelopen launcher houdt het icoon vast; dan moet opstarten toch werken.
    monkeypatch.setattr(launch, "claim_tray", lambda: False)
    answers = iter([False, False, True])
    monkeypatch.setattr(launch, "is_running", lambda: next(answers))
    launch.main([])
    assert (launch.calls["started"], launch.calls["windows"], launch.calls["trays"]) == (1, 1, 0)


def test_own_server_stops_because_another_one_just_started(launch, monkeypatch):
    # Twee keer snel klikken: de tweede server krijgt de poort niet, maar Progen draait wel.
    monkeypatch.setattr(launch, "start_server", lambda: FakeProcess(exit_code=1))
    answers = iter([False, False, False, True])
    monkeypatch.setattr(launch, "is_running", lambda: next(answers))
    launch.main([])
    assert launch.calls["messages"] == [] and launch.calls["windows"] == 1


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


def tray(monkeypatch):
    """launch.pyw waarin acties van het icoon meteen draaien in plaats van op de achtergrond."""
    launch = load("launch.pyw")
    monkeypatch.setattr(launch, "in_background", lambda action: action())
    monkeypatch.setattr(launch, "show_message", lambda *a, **kw: pytest.fail("geen popup vanuit het icoon"))
    return launch


def test_tray_actions_run_in_the_background():
    # Het menu van het icoon mag nooit blokkeren (anders reageert het icoon, en een popup, niet meer).
    import threading
    launch = load("launch.pyw")
    ran_in, done = [], threading.Event()
    launch.in_background(lambda: ran_in.append(threading.current_thread()) or done.set())
    assert done.wait(5) and ran_in[0] is not threading.current_thread()


def test_tray_menu(monkeypatch):
    launch = tray(monkeypatch)
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
    launch = tray(monkeypatch)
    icon = FakeIcon()
    monkeypatch.setattr(launch, "request_stop", lambda: "Progen refused to stop (HTTP 403).")
    dict(launch.tray_menu_actions(icon))["Stop Progen"]()
    assert not icon.stopped and icon.notes == ["Progen refused to stop (HTTP 403)."]  # ballon, geen popup


def test_stop_in_the_tray_when_progen_already_stopped_just_removes_the_icon(monkeypatch):
    from app.desktop import NOT_RUNNING
    launch = tray(monkeypatch)
    icon = FakeIcon()
    monkeypatch.setattr(launch, "request_stop", lambda: NOT_RUNNING)
    dict(launch.tray_menu_actions(icon))["Stop Progen"]()
    assert icon.stopped and icon.notes == []


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
    monkeypatch.setattr(stop, "make_dpi_aware", lambda: None)
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
    messages, dpi = [], []
    monkeypatch.setattr(stop, "show_message", lambda text, **kw: messages.append(text))
    monkeypatch.setattr(stop, "make_dpi_aware", lambda: dpi.append(1))
    stop.main()
    assert messages == ["Progen isn't running."] and dpi == [1]


def test_sync_from_the_tray_returns_the_message_as_text(monkeypatch):
    from app import desktop
    html = '<div class="toast"><span class="toast-icon">x</span><span>Synced with Spotify &#39;now&#39; · 2 new plays</span></div>'.encode()
    sent = []
    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda request, timeout: sent.append(request) or Answer(html))
    assert desktop.sync_spotify().endswith("Synced with Spotify 'now' · 2 new plays")
    assert sent[0].get_header("Hx-request") == "true"


# ---- De server stopt altijd ----

def test_stopping_also_schedules_a_hard_exit_as_safety_net(monkeypatch):
    import app.shutdown as shutdown
    timers = []

    class FakeTimer:
        def __init__(self, seconds, function, args=()):
            self.seconds, self.function, self.args, self.daemon = seconds, function, args, False
            timers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(shutdown.threading, "Timer", FakeTimer)
    shutdown.stop_this_server()
    graceful, hard = timers
    assert graceful.seconds < 1 and graceful.args == [shutdown.signal.SIGINT]
    assert hard.seconds >= 15 and hard.daemon  # daemon: bij een gewone stop verdwijnt hij vanzelf
    assert hard.function is shutdown.force_exit


# ---- Scherpe iconen ----

def test_tray_icon_is_the_real_ico_at_the_small_icon_size(monkeypatch):
    launch = load("launch.pyw")
    loaded = []

    class LoadImage:
        def __call__(self, instance, path, kind, cx, cy, flags):
            loaded.append((path, kind, cx, cy, flags))
            return 1234

    class User32:
        LoadImageW = LoadImage()

        def GetSystemMetrics(self, index):
            return {launch.SM_CXSMICON: 20}[index]  # 125%: 20 px

    monkeypatch.setattr(launch.ctypes, "WinDLL", lambda name, **kw: User32(), raising=False)
    assert launch.tray_icon_handle() == 1234
    assert loaded == [(str(launch.ICON), launch.IMAGE_ICON, 20, 20, launch.LR_LOADFROMFILE)]


def test_tray_icon_falls_back_when_windows_cannot_load_it(monkeypatch):
    launch = load("launch.pyw")
    monkeypatch.setattr(launch.ctypes, "WinDLL", lambda name, **kw: (_ for _ in ()).throw(OSError()), raising=False)
    assert launch.tray_icon_handle() is None  # pystray maakt dan zelf een icoon


def test_page_offers_sharp_png_icons_and_a_manifest():
    import json
    from PIL import Image
    client = TestClient(create_app(":memory:"))
    page = client.get("/").text
    for size in (32, 48, 64, 96):
        href = f"/static/icons/icon-{size}.png"
        assert f'href="{href}" sizes="{size}x{size}"' in page
        with Image.open(ROOT / "app" / "static" / "icons" / f"icon-{size}.png") as png:
            assert png.size == (size, size) and png.mode == "RGBA"
    assert 'rel="manifest" href="/static/manifest.json"' in page
    manifest = json.loads(client.get("/static/manifest.json").text)
    assert manifest["name"] == "Progen" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200


# ---- Herinneringen van Planner bij de klok ----

def test_reminders_are_notified_once_each(monkeypatch):
    import datetime as dt
    launch = load("launch.pyw")
    icon, seen, asked = FakeIcon(), set(), []
    answers = [[{"key": "p/2026-10-09/1", "text": "Tomorrow: Dentist at 14:00"}],
               [{"key": "p/2026-10-09/1", "text": "Tomorrow: Dentist at 14:00"},   # overlap: niet nog eens
                {"key": "g/2026-10-09/2", "text": "In 3 hours: Gym at 18:00"},
                {"key": "s/2026-10-09/2", "text": "Today: Birthday Sam"}]]
    clock = iter([dt.datetime(2026, 10, 8, 14, 0, 30), dt.datetime(2026, 10, 8, 14, 1, 30)])
    monkeypatch.setattr(launch, "datetime_now", lambda: next(clock))

    def fetch(since):
        asked.append(since)
        return answers.pop(0)

    start = dt.datetime(2026, 10, 8, 13, 59, 40)
    since = launch.check_reminders(icon, seen, start, fetch)
    since = launch.check_reminders(icon, seen, since, fetch)
    assert asked == [start, dt.datetime(2026, 10, 8, 14, 0, 30)]  # steeds vanaf de vorige controle
    # Twee tegelijk in één melding (een tweede ballon zou de eerste wegdrukken).
    assert icon.notes == ["Tomorrow: Dentist at 14:00", "In 3 hours: Gym at 18:00\nToday: Birthday Sam"]


def test_reminders_keep_their_window_while_progen_is_unreachable(monkeypatch):
    import datetime as dt
    launch = load("launch.pyw")
    monkeypatch.setattr(launch, "datetime_now", lambda: dt.datetime(2026, 10, 8, 14, 5))
    icon, start = FakeIcon(), dt.datetime(2026, 10, 8, 14, 0)
    assert launch.check_reminders(icon, set(), start, lambda since: None) == start  # niets gemist
    assert icon.notes == []


def test_reminder_watch_starts_from_now_and_stops(monkeypatch):
    import datetime as dt
    import threading
    launch = load("launch.pyw")
    start = dt.datetime(2026, 10, 8, 9, 0)
    monkeypatch.setattr(launch, "datetime_now", lambda: start)
    asked, stopped = [], threading.Event()

    def fetch(since):
        asked.append(since)
        stopped.set()
        return []

    monkeypatch.setattr(launch, "fetch_due", fetch)
    monkeypatch.setattr(launch, "REMINDER_INTERVAL", 0.01)
    launch.watch_reminders(FakeIcon(), stopped)
    assert asked == [start]  # nooit iets van vóór de start van de launcher


def test_due_is_asked_since_the_previous_check(monkeypatch):
    import datetime as dt
    from app import desktop
    sent = []
    monkeypatch.setattr(desktop.urllib.request, "urlopen",
                        lambda request, timeout: sent.append(request) or Answer(b'[{"key": "k", "text": "t"}]'))
    assert desktop.fetch_due(dt.datetime(2026, 10, 8, 14, 0, 30)) == [{"key": "k", "text": "t"}]
    assert sent[0].full_url == "http://127.0.0.1:8000/planner/due?since=2026-10-08T14%3A00%3A30"

    def refused(request, timeout):
        raise desktop.urllib.error.URLError("connection refused")

    monkeypatch.setattr(desktop.urllib.request, "urlopen", refused)
    assert desktop.fetch_due(dt.datetime(2026, 10, 8, 14, 0)) is None
