"""Talks to the local Progen server from the desktop: shared by launch.pyw,
stop.pyw and the tray icon. Standard library only, so it loads quickly
without importing the whole app.
"""
import ctypes
import datetime
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOST, PORT = "127.0.0.1", 8000  # never 0.0.0.0: Progen is only for this computer
URL = f"http://{HOST}:{PORT}"
SHUTDOWN_HEADER = "X-Progen-Shutdown"
STOP_TIMEOUT = 10  # seconds
NOT_RUNNING = "Progen isn't running."


def is_running() -> bool:
    """Does anything answer at Progen's address?"""
    try:
        with urllib.request.urlopen(URL + "/", timeout=1):
            return True
    except urllib.error.HTTPError:
        return True  # something answers, even if it is with an error code
    except (urllib.error.URLError, OSError):
        return False


def request_stop() -> str | None:
    """Asks the server to stop cleanly and waits until it is gone.
    Returns None if that worked, otherwise a message for the user."""
    request = urllib.request.Request(URL + "/admin/shutdown", method="POST", data=b"",
                                     headers={SHUTDOWN_HEADER: "1"})
    try:
        with urllib.request.urlopen(request, timeout=5):
            pass
    except urllib.error.HTTPError as exc:
        return f"Progen refused to stop (HTTP {exc.code})."
    except (urllib.error.URLError, OSError):
        return NOT_RUNNING
    deadline = time.monotonic() + STOP_TIMEOUT
    while is_running() and time.monotonic() < deadline:
        time.sleep(0.25)
    return "Progen is still running. Try again, or close it from Task Manager." if is_running() else None


def fetch_due(since: datetime.datetime) -> list[dict] | None:
    """Planner reminders since `since` (local time). None if Progen doesn't answer."""
    query = urllib.parse.urlencode({"since": since.replace(microsecond=0).isoformat()})
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{URL}/planner/due?{query}"), timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def sync_spotify() -> str:
    """The same as "Sync now" in Settings; returns the server's message as text."""
    request = urllib.request.Request(URL + "/ui/listening/spotify/sync", method="POST", data=b"",
                                     headers={"HX-Request": "true"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            page = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return NOT_RUNNING
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)).strip()
    return html.unescape(text) or "Synced."


PER_MONITOR_AWARE_V2 = -4  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2


def make_dpi_aware() -> None:
    """Tells Windows that this process handles display scaling (e.g. 125%) itself. Otherwise
    Windows draws the icon's menu and the notifications at 100% and stretches them: blurry.
    Must happen before there is a window or icon. On older Windows the older variants;
    if nothing works, everything stays as it was."""
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(PER_MONITOR_AWARE_V2)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # Vista: system DPI only
    except (AttributeError, OSError):
        pass


def show_message(text: str, error: bool = True) -> None:
    """Without a console (pythonw) a message box is the only way to say something.
    Don't use it from the tray icon: there the box blocks the icon
    (and stops responding itself). The icon uses icon.notify."""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "Progen", 0x10 if error else 0x40)
    except (AttributeError, OSError):
        print(text, file=sys.stderr)
