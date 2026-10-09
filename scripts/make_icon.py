"""Maakt de iconen uit app/static/icon.svg:

- app/static/icon.ico (16 t/m 256 px): de snelkoppeling en het icoon bij de klok;
- app/static/icons/icon-<maat>.png: het favicon en manifest van de pagina. Het Progen-venster
  (Edge --app) gebruikt die voor de taakbalk; met alleen een SVG kiest Edge een klein plaatje
  en rekt het op, en dan is het icoon wazig. De favicons (32-96 px) komen uit icon-small.svg,
  een vereenvoudigde versie: de fijne details van icon.svg vallen op die maat samen.

Pillow kan geen SVG lezen. Daarom rendert een headless Edge of Chrome het SVG
eerst als transparante PNG van 512×512; Pillow maakt daar de rest van.
Alleen nodig als het icoon verandert:

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
SMALL_SVG = ROOT / "app" / "static" / "icon-small.svg"  # voor de favicons (taakbalk)
ICO = ROOT / "app" / "static" / "icon.ico"
PNG_DIR = ROOT / "app" / "static" / "icons"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
FAVICON_SIZES = [32, 48, 64, 96]  # uit icon-small.svg; zie index.html
PNG_SIZES = [192, 256, 512]  # uit icon.svg; zie manifest.json
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
