"""Makes the icons from app/static/icon.svg:

- app/static/icon.ico (16 to 256 px): the shortcut and the tray icon;
- app/static/icons/icon-<size>.png: the page's favicon and manifest. The Progen window
  (Edge --app) uses those for the taskbar; with only an SVG, Edge picks a small image
  and stretches it, and then the icon is blurry. The favicons (32-96 px) come from icon-small.svg,
  a simplified version: the fine details of icon.svg merge together at that size.

Pillow can't read SVG. So a headless Edge or Chrome first renders the SVG
as a transparent 512×512 PNG; Pillow makes the rest from that.
Only needed when the icon changes:

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
SMALL_SVG = ROOT / "app" / "static" / "icon-small.svg"  # for the favicons (taskbar)
ICO = ROOT / "app" / "static" / "icon.ico"
PNG_DIR = ROOT / "app" / "static" / "icons"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
FAVICON_SIZES = [32, 48, 64, 96]  # from icon-small.svg; see index.html
PNG_SIZES = [192, 256, 512]  # from icon.svg; see manifest.json
RENDER = 512

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


def render_png(work: Path, svg: Path = SVG) -> Path:
    page = work / f"{svg.stem}.html"
    page.write_text(
        '<!doctype html><html><body style="margin:0;background:transparent">'
        f'<img src="{svg.as_uri()}" width="{RENDER}" height="{RENDER}" style="display:block">'
        "</body></html>", encoding="utf-8")
    png = work / f"{svg.stem}.png"
    subprocess.run([
        find_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--default-background-color=00000000",
        f"--window-size={RENDER},{RENDER}", f"--user-data-dir={work / 'profile'}",
        "--allow-file-access-from-files", f"--screenshot={png}", page.as_uri(),
    ], check=True, capture_output=True, timeout=60)
    return png


def rendered(work: Path, svg: Path) -> Image.Image:
    with Image.open(render_png(work, svg)) as png:
        return png.convert("RGBA").resize((RENDER, RENDER), Image.Resampling.LANCZOS)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        image, small = rendered(Path(tmp), SVG), rendered(Path(tmp), SMALL_SVG)
    image.save(ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    PNG_DIR.mkdir(exist_ok=True)
    for source, sizes in [(small, FAVICON_SIZES), (image, PNG_SIZES)]:
        for size in sizes:
            source.resize((size, size), Image.Resampling.LANCZOS).save(PNG_DIR / f"icon-{size}.png", optimize=True)
    print(f"Wrote {ICO.relative_to(ROOT)} ({', '.join(map(str, SIZES))} px)")
    print(f"Wrote {PNG_DIR.relative_to(ROOT)}/icon-*.png: favicons {FAVICON_SIZES} from {SMALL_SVG.name}, "
          f"{PNG_SIZES} from {SVG.name}")


if __name__ == "__main__":
    main()
