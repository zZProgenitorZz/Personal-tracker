"""Cover images: downloading, checking, making them uniform and storing them locally.

Every cover (downloaded, uploaded or from a pasted URL) goes through
process_image and ends up as a 300×450 WebP with a UUID name in the covers folder.
That way a cover keeps working, even if the original site changes.
Shared by all domains; searching for covers is per domain.
"""
import re
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .domain import DomainError

COVER_SIZE = (300, 450)          # 2:3, portrait like a book cover
MAX_BYTES = 5 * 1024 * 1024      # 5 MB
DOWNLOAD_TIMEOUT = 10.0          # seconds
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
BACKGROUND = (21, 18, 31)        # behind transparent parts, the same tint as the app

Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse "decompression bombs"

_COVER_NAME = re.compile(r"[0-9a-f]{32}\.webp")


class CoverError(Exception):
    """A cover that can't be used. The message is meant for the user."""


def too_large() -> CoverError:
    return CoverError(f"The image is larger than {MAX_BYTES // (1024 * 1024)} MB")


def process_image(data: bytes, size: tuple[int, int] = COVER_SIZE) -> bytes:
    """Checks that `data` is an image and turns it into a WebP of `size`
    (300×450 by default, a cover; the Spotify profile photo uses a square)."""
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

    # Crop from the center, so never stretch.
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
        """Processes the image and stores it. Returns the file name."""
        webp = process_image(data)
        name = f"{uuid.uuid4().hex}.webp"
        (self.directory / name).write_bytes(webp)
        return name

    def save_from_url(self, url: str) -> str:
        return self.save(self.download(url))

    def delete(self, name: str) -> None:
        # Only our own covers, so a strange name never reaches outside the folder.
        if _COVER_NAME.fullmatch(name):
            (self.directory / name).unlink(missing_ok=True)

    def download(self, url: str) -> bytes:
        return download(self._client, url)


def download(client: httpx.Client, url: str, timeout: float = DOWNLOAD_TIMEOUT) -> bytes:
    """Downloads an image of at most MAX_BYTES. Raises CoverError with a message for the user."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CoverError("Only http and https links can be used")
    try:
        with client.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
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


def with_cover(covers: CoverStore, cover_name: str | None, action):
    """Runs action, the command that uses a freshly saved cover. If the command is refused
    (DomainError), the cover is deleted again so no loose files stay behind, and the error is raised."""
    try:
        return action()
    except DomainError:
        if cover_name:
            covers.delete(cover_name)
        raise
