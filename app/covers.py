"""Cover-afbeeldingen: downloaden, controleren, gelijk maken en lokaal bewaren.

Elke cover (gedownload, geüpload of via een geplakte URL) gaat door
process_image en komt als 300×450 WebP met een UUID-naam in de covermap.
Zo blijft een cover werken, ook als de oorspronkelijke site verandert.
Gedeeld door alle domeinen; het zoeken naar covers is per domein.
"""
import re
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

COVER_SIZE = (300, 450)          # 2:3, staand zoals een boekcover
MAX_BYTES = 5 * 1024 * 1024      # 5 MB
DOWNLOAD_TIMEOUT = 10.0          # seconden
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
BACKGROUND = (21, 18, 31)        # achter transparante delen, zelfde tint als de app

Image.MAX_IMAGE_PIXELS = 40_000_000  # weiger "decompression bombs"

_COVER_NAME = re.compile(r"[0-9a-f]{32}\.webp")


class CoverError(Exception):
    """Een cover die niet gebruikt kan worden. De melding is bedoeld voor de gebruiker."""


def too_large() -> CoverError:
    return CoverError(f"The image is larger than {MAX_BYTES // (1024 * 1024)} MB")


def process_image(data: bytes, size: tuple[int, int] = COVER_SIZE) -> bytes:
    """Controleer dat `data` een afbeelding is en maak er een WebP van `size` van
    (standaard 300×450, een cover; de Spotify-profielfoto gebruikt een vierkant)."""
    if len(data) > MAX_BYTES:
        raise too_large()
    try:
        with Image.open(BytesIO(data)) as probe:
            image_format = probe.format
            probe.verify()
        if image_format not in ALLOWED_FORMATS:
            raise CoverError("Use a JPEG, PNG, WebP, GIF or BMP image")
        image = Image.open(BytesIO(data))
        image.load()
    except CoverError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise CoverError("That file isn't a valid image") from exc

    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, BACKGROUND)
        image.paste(rgba, mask=rgba.getchannel("A"))
    else:
        image = image.convert("RGB")

    # Bijsnijden vanuit het midden, dus nooit uitrekken.
    image = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    out = BytesIO()
    image.save(out, "WEBP", quality=85, method=6)
    return out.getvalue()


class CoverStore:
    def __init__(self, directory: Path, client: httpx.Client):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._client = client

    def save(self, data: bytes) -> str:
        """Verwerk de afbeelding en bewaar hem. Geeft de bestandsnaam terug."""
        webp = process_image(data)
        name = f"{uuid.uuid4().hex}.webp"
        (self.directory / name).write_bytes(webp)
        return name

    def save_from_url(self, url: str) -> str:
        return self.save(self.download(url))

    def delete(self, name: str) -> None:
        # Alleen eigen covers, zodat een vreemde naam nooit buiten de map komt.
        if _COVER_NAME.fullmatch(name):
            (self.directory / name).unlink(missing_ok=True)

    def download(self, url: str) -> bytes:
        url = url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise CoverError("Only http and https links can be used")
        try:
            with self._client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as response:
                if response.status_code != 200:
                    raise CoverError(f"The image couldn't be downloaded (HTTP {response.status_code})")
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind and not kind.startswith("image/") and kind != "application/octet-stream":
                    raise CoverError("That link doesn't point to an image")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_BYTES:
                    raise too_large()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data += chunk
                    if len(data) > MAX_BYTES:
                        raise too_large()
        except httpx.TimeoutException as exc:
            raise CoverError("The image took too long to download") from exc
        except httpx.HTTPError as exc:
            raise CoverError("Couldn't reach that address. Check the link or your connection.") from exc
        return bytes(data)
