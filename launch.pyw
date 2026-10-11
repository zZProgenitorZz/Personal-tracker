"""Starts Progen as an ordinary background app (shortcut, or automatically when signing in).

Runs with pythonw, so without a console window:
1. If nothing answers at http://127.0.0.1:8000 yet, uvicorn starts in the
   background (only on 127.0.0.1, without --reload), with its output in data/launcher.log.
2. Progen opens as its own window in Edge (--app), or in your default browser.
   With --background (starting automatically when signing in) no window opens.
3. A Progen icon appears in the system tray (by the clock, under "hidden
   icons"): Open Progen, Sync Spotify now, Stop Progen. There is only ever
   one such icon; if you click the shortcut again, only a window opens.

Closing the window doesn't stop the server; Stop Progen does that.

The icon never shows a popup (MessageBox): it would block the icon's thread,
so that the icon and the popup both stop responding. Notifications go out as balloons.

Planner reminders: every minute the icon asks /planner/due (since the previous
time) and shows them as notifications. It remembers what it already reported in memory;
after a restart it reports nothing from before the start. Only while Progen is running.
"""
import ctypes
import datetime
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # so app.desktop can be found, also when starting automatically

from app.desktop import (  # noqa: E402
    HOST, NOT_RUNNING, PORT, URL, fetch_due, is_running, make_dpi_aware, request_stop, show_message, sync_spotify,
)

LOG = ROOT / "data" / "launcher.log"
LOG_MAX_BYTES = 1_000_000  # larger: the old log becomes launcher.log.old
ICON = ROOT / "app" / "static" / "icon.ico"
START_TIMEOUT = 15  # seconds
GRACEFUL_SHUTDOWN = 5  # seconds uvicorn gets to stop; after that it closes open connections
EXITED_GRACE_CHECKS = 12  # if our server stops right away, check for ~3 s more whether another one is just starting
WATCH_INTERVAL = 5  # seconds: if the server disappears (e.g. through stop.pyw), the icon disappears too
REMINDER_INTERVAL = 60  # seconds between two checks for Planner reminders
TRAY_MUTEX = "Local\\ProgenTrayIcon"
SM_CXSMICON, IMAGE_ICON, LR_LOADFROMFILE = 49, 1, 0x10  # Windows constants for the tray icon
EDGE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

_mutex = None  # stays open as long as this launcher manages the icon


# ---- The server ----

def python_executable() -> Path:
    """Preferably the Python from .venv (with all packages); otherwise the Python running this script."""
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return venv
    current = Path(sys.executable)
    console = current.with_name("python.exe")  # pythonw.exe -> python.exe (same folder)
    return console if console.exists() else current


def server_command() -> list[str]:
    return [str(python_executable()), "-m", "uvicorn", "app.main:create_app", "--factory",
            "--host", HOST, "--port", str(PORT),
            # Without a limit uvicorn can wait forever for a connection from the window that
            # dropped (ConnectionResetError on Windows); then the process hangs.
            "--timeout-graceful-shutdown", str(GRACEFUL_SHUTDOWN)]


def start_server() -> subprocess.Popen:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > LOG_MAX_BYTES:
        LOG.replace(LOG.with_suffix(".log.old"))
    log = open(LOG, "a", encoding="utf-8")  # stays open as long as the server runs
    log.write(f"\n==== Progen gestart {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ====\n")
    log.flush()
    return subprocess.Popen(
        server_command(), cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        # No console window, and the server keeps running when this script stops.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def wait_until_running(process: subprocess.Popen | None) -> bool:
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if is_running():
            return True
        if process is not None and process.poll() is not None:
            # Stopped right away: an error in the code, or the port is already taken by a server that
            # another click just started. Check for a moment whether Progen answers after all.
            for _ in range(EXITED_GRACE_CHECKS):
                if is_running():
                    return True
                time.sleep(0.25)
            return False
        time.sleep(0.25)
    return is_running()


# ---- The window ----

def find_edge() -> str | None:
    for path in EDGE_PATHS:
        if path.exists():
            return str(path)
    return shutil.which("msedge")


def open_window() -> None:
    edge = find_edge()
    if edge:
        subprocess.Popen([edge, f"--app={URL}"])
    else:
        webbrowser.open(URL)


# ---- The tray icon ----

def claim_tray() -> bool:
    """True if this launcher may manage the icon; False if there already is one."""
    global _mutex
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return True  # not Windows: no mutex, just carry on
    _mutex = kernel32.CreateMutexW(None, False, TRAY_MUTEX)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def in_background(action) -> None:
    """Menu actions run outside the icon's thread, so the icon keeps responding."""
    threading.Thread(target=action, daemon=True).start()


def tray_menu_actions(icon) -> list[tuple[str, object]]:
    """The buttons in the menu (right-click). The first is also the double-click.
    Notifications as balloons (icon.notify), never as popups."""
    def open_progen():
        open_window()

    def sync_now():
        in_background(lambda: icon.notify(sync_spotify(), "Progen"))

    def stop_progen():
        def stop():
            problem = request_stop()
            if problem and problem != NOT_RUNNING:
                icon.notify(problem, "Progen")  # the icon stays, so you can try again
            else:
                icon.stop()  # stopped, or wasn't running anymore: nothing left to manage
        in_background(stop)

    return [("Open Progen", open_progen), ("Sync Spotify now", sync_now), ("Stop Progen", stop_progen)]


def watch_server(icon, stopped: threading.Event) -> None:
    """Removes the icon as soon as the server is gone (e.g. stopped through stop.pyw)."""
    while not stopped.wait(WATCH_INTERVAL):
        if not is_running():
            icon.stop()
            return


def tray_icon_handle(icon_path: Path = ICON):
    """Loads icon.ico at exactly the size of a tray icon (20 px at 125%).
    The .ico has its own sharp version per size; Windows picks the right one. Returns None
    if that fails (then pystray makes an icon from the image itself)."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except (AttributeError, OSError):
        return None
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    size = user32.GetSystemMetrics(SM_CXSMICON)  # after make_dpi_aware: the real size on this screen
    return user32.LoadImageW(None, str(icon_path), IMAGE_ICON, size, size, LR_LOADFROMFILE) or None


def datetime_now() -> datetime.datetime:
    return datetime.datetime.now()  # local time, like the plans; tests pin it


def check_reminders(icon, seen: set, since: datetime.datetime, fetch=None) -> datetime.datetime:
    """Reports new reminders since `since` and returns from when the next check looks.
    Several at once in one notification: a second balloon would push the first away immediately."""
    now = datetime_now()
    due = (fetch or fetch_due)(since)
    if due is None:
        return since  # Progen isn't answering for a moment: next time from the same moment
    new = [item for item in due if item["key"] not in seen]
    seen.update(item["key"] for item in new)
    if new:
        icon.notify("\n".join(item["text"] for item in new), "Progen")
    return now


def watch_reminders(icon, stopped: threading.Event) -> None:
    """Checks every minute, from the moment the icon exists (so nothing from before the start)."""
    seen: set = set()
    since = datetime_now()
    while not stopped.wait(REMINDER_INTERVAL):
        since = check_reminders(icon, seen, since, fetch_due)


def run_tray() -> None:
    import pystray
    from PIL import Image

    class SharpIcon(pystray.Icon):
        """pystray makes its own .ico from one image, without the 20 px version: blurry at 125%.
        So we pass the real icon.ico, loaded at the size Windows wants."""
        def _assert_icon_handle(self):
            if not self._icon_handle:
                self._icon_handle = tray_icon_handle()
            if not self._icon_handle:
                super()._assert_icon_handle()

    with Image.open(ICON) as ico:
        ico.size = (64, 64)  # the 64 px version from the .ico
        image = ico.convert("RGBA")
    icon = SharpIcon("Progen", image, "Progen")
    actions = dict(tray_menu_actions(icon))
    icon.menu = pystray.Menu(
        pystray.MenuItem("Open Progen", lambda: actions["Open Progen"](), default=True),
        pystray.MenuItem("Sync Spotify now", lambda: actions["Sync Spotify now"]()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop Progen", lambda: actions["Stop Progen"]()),
    )
    stopped = threading.Event()
    threading.Thread(target=watch_server, args=(icon, stopped), daemon=True).start()
    threading.Thread(target=watch_reminders, args=(icon, stopped), daemon=True).start()
    try:
        icon.run()
    finally:
        stopped.set()


# ---- Together ----

def main(argv: list[str] | None = None) -> None:
    background = "--background" in (sys.argv[1:] if argv is None else argv)
    make_dpi_aware()  # before the icon and notifications, otherwise they are blurry at display scaling > 100%
    owner = claim_tray()
    if not is_running():
        # Even if another launcher still has the icon (a stuck one, for example):
        # then this one starts the server. If two start at once, only one gets the port.
        process = start_server()
        if not wait_until_running(process):
            show_message(f"Progen didn't start within {START_TIMEOUT} seconds.\n\nSee {LOG} for what went wrong.")
            return
    if not background:
        open_window()
    if owner:
        run_tray()


if __name__ == "__main__":
    main()
