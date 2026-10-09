"""Start Progen als een gewone achtergrond-app (snelkoppeling, of automatisch bij aanmelden).

Draait met pythonw, dus zonder consolevenster:
1. Antwoordt er nog niets op http://127.0.0.1:8000, dan start uvicorn op de
   achtergrond (alleen op 127.0.0.1, zonder --reload), met de uitvoer in data/launcher.log.
2. Progen opent als eigen venster in Edge (--app), of in je standaardbrowser.
   Met --background (automatisch starten bij aanmelden) opent er geen venster.
3. Er komt een Progen-icoon in het systeemvak (bij de klok, onder "verborgen
   pictogrammen"): Open Progen, Sync Spotify now, Stop Progen. Er is altijd maar
   één zo'n icoon; klik je nog eens op de snelkoppeling, dan opent alleen een venster.

Het venster sluiten stopt de server niet; dat doe je met Stop Progen.

Het icoon toont nooit een popup (MessageBox): die zou de thread van het icoon
blokkeren, waardoor icoon en popup allebei niet meer reageren. Meldingen gaan als ballon.

Herinneringen van Planner: elke minuut vraagt het icoon /planner/due op (sinds de vorige
keer) en toont ze als melding. Wat al gemeld is, onthoudt het in het geheugen; na een
herstart meldt het niets van vóór de start. Alleen zolang Progen draait.
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
sys.path.insert(0, str(ROOT))  # zodat app.desktop te vinden is, ook bij automatisch starten

from app.desktop import (  # noqa: E402
    HOST, NOT_RUNNING, PORT, URL, fetch_due, is_running, make_dpi_aware, request_stop, show_message, sync_spotify,
)

LOG = ROOT / "data" / "launcher.log"
LOG_MAX_BYTES = 1_000_000  # groter: het oude log wordt launcher.log.old
ICON = ROOT / "app" / "static" / "icon.ico"
START_TIMEOUT = 15  # seconden
GRACEFUL_SHUTDOWN = 5  # seconden die uvicorn krijgt om te stoppen; daarna sluit hij open verbindingen af
EXITED_GRACE_CHECKS = 12  # stopt onze server meteen, kijk dan nog ~3 s of een andere net opstart
WATCH_INTERVAL = 5  # seconden: verdwijnt de server (bv. via stop.pyw), dan verdwijnt het icoon ook
REMINDER_INTERVAL = 60  # seconden tussen twee keer kijken naar herinneringen van Planner
TRAY_MUTEX = "Local\\ProgenTrayIcon"
SM_CXSMICON, IMAGE_ICON, LR_LOADFROMFILE = 49, 1, 0x10  # Windows-constanten voor het icoon bij de klok
EDGE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

_mutex = None  # blijft open zolang deze launcher het icoon beheert


# ---- De server ----

def python_executable() -> Path:
    """Liefst de Python uit .venv (met alle pakketten); anders de Python die dit script draait."""
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return venv
    current = Path(sys.executable)
    console = current.with_name("python.exe")  # pythonw.exe -> python.exe (zelfde map)
    return console if console.exists() else current


def server_command() -> list[str]:
    return [str(python_executable()), "-m", "uvicorn", "app.main:create_app", "--factory",
            "--host", HOST, "--port", str(PORT),
            # Zonder grens kan uvicorn eeuwig wachten op een verbinding van het venster die
            # weggevallen is (ConnectionResetError op Windows); dan blijft het proces hangen.
            "--timeout-graceful-shutdown", str(GRACEFUL_SHUTDOWN)]


def start_server() -> subprocess.Popen:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > LOG_MAX_BYTES:
        LOG.replace(LOG.with_suffix(".log.old"))
    log = open(LOG, "a", encoding="utf-8")  # blijft open zolang de server draait
    log.write(f"\n==== Progen gestart {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ====\n")
    log.flush()
    return subprocess.Popen(
        server_command(), cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        # Geen consolevenster, en de server blijft draaien als dit script stopt.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def wait_until_running(process: subprocess.Popen | None) -> bool:
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if is_running():
            return True
        if process is not None and process.poll() is not None:
            # Meteen gestopt: een fout in de code, of de poort is al bezet door een server die
            # net door een andere klik is gestart. Kijk nog even of Progen alsnog antwoordt.
            for _ in range(EXITED_GRACE_CHECKS):
                if is_running():
                    return True
                time.sleep(0.25)
            return False
        time.sleep(0.25)
    return is_running()


# ---- Het venster ----

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


# ---- Het icoon in het systeemvak ----

def claim_tray() -> bool:
    """True als deze launcher het icoon mag beheren; False als er al één is."""
    global _mutex
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return True  # geen Windows: geen mutex, gewoon doorgaan
    _mutex = kernel32.CreateMutexW(None, False, TRAY_MUTEX)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def in_background(action) -> None:
    """Acties uit het menu draaien buiten de thread van het icoon, zodat het icoon blijft reageren."""
    threading.Thread(target=action, daemon=True).start()


def tray_menu_actions(icon) -> list[tuple[str, object]]:
    """De knoppen in het menu (rechtsklik). De eerste is ook de dubbelklik.
    Meldingen als ballon (icon.notify), nooit als popup."""
    def open_progen():
        open_window()

    def sync_now():
        in_background(lambda: icon.notify(sync_spotify(), "Progen"))

    def stop_progen():
        def stop():
            problem = request_stop()
            if problem and problem != NOT_RUNNING:
                icon.notify(problem, "Progen")  # het icoon blijft, zodat je het nog eens kunt proberen
            else:
                icon.stop()  # gestopt, of draaide al niet meer: niets meer te beheren
        in_background(stop)

    return [("Open Progen", open_progen), ("Sync Spotify now", sync_now), ("Stop Progen", stop_progen)]


def watch_server(icon, stopped: threading.Event) -> None:
    """Haal het icoon weg zodra de server er niet meer is (bv. gestopt via stop.pyw)."""
    while not stopped.wait(WATCH_INTERVAL):
        if not is_running():
            icon.stop()
            return


def tray_icon_handle(icon_path: Path = ICON):
    """Laad icon.ico op precies de maat van een icoon in het systeemvak (20 px bij 125%).
    Het .ico heeft per maat een eigen, scherpe versie; Windows kiest de passende. Geeft None
    als het niet lukt (dan maakt pystray zelf een icoon uit het plaatje)."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except (AttributeError, OSError):
        return None
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    size = user32.GetSystemMetrics(SM_CXSMICON)  # na make_dpi_aware: de echte maat op dit scherm
    return user32.LoadImageW(None, str(icon_path), IMAGE_ICON, size, size, LR_LOADFROMFILE) or None


def datetime_now() -> datetime.datetime:
    return datetime.datetime.now()  # lokale tijd, net als de plannen; tests zetten hem vast


def check_reminders(icon, seen: set, since: datetime.datetime, fetch=None) -> datetime.datetime:
    """Meld nieuwe herinneringen sinds `since` en geef terug vanaf wanneer de volgende keer kijkt.
    Meerdere tegelijk in één melding: een tweede ballon zou de eerste meteen wegdrukken."""
    now = datetime_now()
    due = (fetch or fetch_due)(since)
    if due is None:
        return since  # Progen antwoordt even niet: de volgende keer vanaf hetzelfde moment
    new = [item for item in due if item["key"] not in seen]
    seen.update(item["key"] for item in new)
    if new:
        icon.notify("\n".join(item["text"] for item in new), "Progen")
    return now


def watch_reminders(icon, stopped: threading.Event) -> None:
    """Elke minuut kijken, vanaf het moment dat het icoon er is (dus niets van vóór de start)."""
    seen: set = set()
    since = datetime_now()
    while not stopped.wait(REMINDER_INTERVAL):
        since = check_reminders(icon, seen, since, fetch_due)


def run_tray() -> None:
    import pystray
    from PIL import Image

    class SharpIcon(pystray.Icon):
        """pystray maakt zelf een .ico uit één plaatje, zonder de 20 px-versie: wazig bij 125%.
        Daarom geven we het echte icon.ico mee, geladen op de maat die Windows wil."""
        def _assert_icon_handle(self):
            if not self._icon_handle:
                self._icon_handle = tray_icon_handle()
            if not self._icon_handle:
                super()._assert_icon_handle()

    with Image.open(ICON) as ico:
        ico.size = (64, 64)  # de 64-px-versie uit het .ico
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


# ---- Samen ----

def main(argv: list[str] | None = None) -> None:
    background = "--background" in (sys.argv[1:] if argv is None else argv)
    make_dpi_aware()  # vóór het icoon en meldingen, anders zijn die wazig bij schermschaal > 100%
    owner = claim_tray()
    if not is_running():
        # Ook als een andere launcher het icoon nog heeft (bijvoorbeeld een vastgelopen):
        # dan start deze de server. Starten er twee tegelijk, dan krijgt er maar één de poort.
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
