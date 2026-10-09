"""Maakt app/static/icon.ico uit app/static/icon.svg (voor de snelkoppeling op het bureaublad).

Pillow kan geen SVG lezen. Daarom rendert een headless Edge of Chrome het SVG
eerst als transparante PNG van 256×256; Pillow maakt daar een .ico van met de
maten 16 t/m 256 px. Alleen nodig als het icoon verandert:

    .venv\\Scripts\\python.exe scripts\\make_icon.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "app" / "static" / "icon.svg"
ICO = ROOT / "app" / "static" / "icon.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
RENDER = 256

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str:
    for candidate in [*BROWSERS, shutil.which("msedge"), shutil.which("chrome")]:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("No Edge or Chrome found to render the SVG.")


def render_png(work: Path) -> Path:
    page = work / "icon.html"
    page.write_text(
        '<!doctype html><html><body style="margin:0;background:transparent">'
        f'<img src="{SVG.as_uri()}" width="{RENDER}" height="{RENDER}" style="display:block">'
        "</body></html>", encoding="utf-8")
    png = work / "icon.png"
    subprocess.run([
        find_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--default-background-color=00000000",
        f"--window-size={RENDER},{RENDER}", f"--user-data-dir={work / 'profile'}",
        "--allow-file-access-from-files", f"--screenshot={png}", page.as_uri(),
    ], check=True, capture_output=True, timeout=60)
    return png


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with Image.open(render_png(Path(tmp))) as rendered:
            image = rendered.convert("RGBA").resize((RENDER, RENDER), Image.Resampling.LANCZOS)
        image.save(ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Wrote {ICO.relative_to(ROOT)} ({', '.join(map(str, SIZES))} px)")


if __name__ == "__main__":
    main()
