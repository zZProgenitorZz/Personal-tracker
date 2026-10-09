"""De lokale Progen-server aanspreken vanaf het bureaublad: gedeeld door launch.pyw,
stop.pyw en het icoon in het systeemvak. Alleen standaardbibliotheek, zodat het
snel laadt zonder de hele app te importeren.
"""
import ctypes
import re
import sys
import time
import urllib.error
import urllib.request

HOST, PORT = "127.0.0.1", 8000  # nooit 0.0.0.0: Progen is alleen voor deze computer
URL = f"http://{HOST}:{PORT}"
SHUTDOWN_HEADER = "X-Progen-Shutdown"
STOP_TIMEOUT = 10  # seconden
NOT_RUNNING = "Progen isn't running."


def is_running() -> bool:
    """Antwoordt er iets op het adres van Progen?"""
    try:
        with urllib.request.urlopen(URL + "/", timeout=1):
            return True
    except urllib.error.HTTPError:
        return True  # er antwoordt iets, al is het met een foutcode
    except (urllib.error.URLError, OSError):
        return False


def request_stop() -> str | None:
    """Vraag de server netjes te stoppen en wacht tot hij weg is.
    Geeft None als het gelukt is, anders een melding voor de gebruiker."""
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


def sync_spotify() -> str:
    """Hetzelfde als "Sync now" in Settings; geeft de melding van de server als tekst terug."""
    request = urllib.request.Request(URL + "/ui/listening/spotify/sync", method="POST", data=b"",
                                     headers={"HX-Request": "true"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return NOT_RUNNING
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return text.replace("&#39;", "'").replace("&amp;", "&") or "Synced."


PER_MONITOR_AWARE_V2 = -4  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2


def make_dpi_aware() -> None:
    """Zeg Windows dat dit proces zelf met schermschaal (bv. 125%) omgaat. Anders tekent
    Windows het menu van het icoon en de meldingen op 100% en rekt ze op: wazig.
    Moet gebeuren voordat er een venster of icoon is. Op oudere Windows de oudere varianten;
    lukt niets, dan blijft alles zoals het was."""
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
        ctypes.windll.user32.SetProcessDPIAware()  # Vista: alleen systeem-DPI
    except (AttributeError, OSError):
        pass


def show_message(text: str, error: bool = True) -> None:
    """Zonder console (pythonw) is een meldingsvenster de enige manier om iets te zeggen.
    Niet gebruiken vanuit het icoon in het systeemvak: daar blokkeert het venster het icoon
    (en reageert het zelf niet meer). Het icoon gebruikt icon.notify."""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "Progen", 0x10 if error else 0x40)
    except (AttributeError, OSError):
        print(text, file=sys.stderr)
