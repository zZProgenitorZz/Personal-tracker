from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.covers import COVER_SIZE, MAX_BYTES, CoverError, CoverStore, process_image


def image_bytes(size=(200, 300), color="red", fmt="PNG", mode="RGB", **save) -> bytes:
    out = BytesIO()
    Image.new(mode, size, color).save(out, fmt, **save)
    return out.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


def close_to(pixel, expected, tolerance=40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, expected))


# ---- Verwerken ----

def test_valid_image_becomes_fixed_size_webp():
    result = decode(process_image(image_bytes(size=(640, 960))))
    assert result.format == "WEBP"
    assert result.size == COVER_SIZE
    assert result.mode == "RGB"


def test_landscape_image_is_cropped_from_the_middle_not_stretched():
    source = Image.new("RGB", (600, 300), "green")
    source.paste((255, 0, 0), (200, 0, 400, 300))  # middelste derde rood
    source.paste((0, 0, 255), (400, 0, 600, 300))
    out = BytesIO()
    source.save(out, "PNG")

    result = decode(process_image(out.getvalue())).convert("RGB")
    for corner in [(5, 5), (COVER_SIZE[0] - 5, COVER_SIZE[1] - 5)]:
        assert close_to(result.getpixel(corner), (255, 0, 0))


def test_exif_orientation_is_applied():
    # Bovenkant rood, onderkant blauw, met EXIF "draai 90° met de klok mee".
    source = Image.new("RGB", (300, 200), "blue")
    source.paste((255, 0, 0), (0, 0, 300, 100))
    exif = Image.Exif()
    exif[0x0112] = 6
    out = BytesIO()
    source.save(out, "JPEG", exif=exif.tobytes())

    result = decode(process_image(out.getvalue())).convert("RGB")
    assert close_to(result.getpixel((30, 225)), (0, 0, 255))
    assert close_to(result.getpixel((270, 225)), (255, 0, 0))


@pytest.mark.parametrize("mode, color", [("RGBA", (255, 0, 0, 128)), ("P", 3), ("L", 128)])
def test_other_color_modes_become_rgb(mode, color):
    assert decode(process_image(image_bytes(mode=mode, color=color))).mode == "RGB"


def test_not_an_image_is_rejected():
    with pytest.raises(CoverError, match="isn't a valid image"):
        process_image(b"<html>dit is geen plaatje</html>")


def test_unsupported_image_format_is_rejected():
    with pytest.raises(CoverError, match="JPEG, PNG"):
        process_image(image_bytes(fmt="TIFF"))


def test_too_large_file_is_rejected():
    with pytest.raises(CoverError, match="5 MB"):
        process_image(b"\0" * (MAX_BYTES + 1))


# ---- Opslaan en downloaden ----

def make_store(tmp_path, handler=None) -> tuple[CoverStore, list]:
    requests = []

    def record(request: httpx.Request):
        requests.append(request)
        return handler(request) if handler else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return CoverStore(tmp_path, client), requests


def test_saved_cover_gets_a_uuid_name(tmp_path):
    store, _ = make_store(tmp_path)
    name = store.save(image_bytes())
    assert name.endswith(".webp") and len(name) == 32 + len(".webp")
    assert decode((tmp_path / name).read_bytes()).size == COVER_SIZE


def test_download_from_url(tmp_path):
    png = image_bytes()
    store, requests = make_store(
        tmp_path, lambda r: httpx.Response(200, content=png, headers={"Content-Type": "image/png"})
    )
    name = store.save_from_url("https://example.com/cover.png")
    assert (tmp_path / name).exists()
    assert str(requests[0].url) == "https://example.com/cover.png"


@pytest.mark.parametrize("url", ["ftp://example.com/a.png", "file:///etc/passwd", "javascript:alert(1)", "geen url"])
def test_only_http_and_https_are_allowed(tmp_path, url):
    store, requests = make_store(tmp_path)
    with pytest.raises(CoverError, match="http"):
        store.save_from_url(url)
    assert requests == []


def test_download_that_is_not_an_image(tmp_path):
    store, _ = make_store(
        tmp_path, lambda r: httpx.Response(200, text="<html></html>", headers={"Content-Type": "text/html"})
    )
    with pytest.raises(CoverError, match="doesn't point to an image"):
        store.save_from_url("https://example.com/page")


def test_download_that_is_too_large(tmp_path):
    store, _ = make_store(
        tmp_path, lambda r: httpx.Response(200, content=b"\0" * (MAX_BYTES + 10), headers={"Content-Type": "image/png"})
    )
    with pytest.raises(CoverError, match="5 MB"):
        store.save_from_url("https://example.com/huge.png")
    assert list(tmp_path.iterdir()) == []


def test_download_timeout(tmp_path):
    def slow(request):
        raise httpx.ReadTimeout("te traag", request=request)

    store, _ = make_store(tmp_path, slow)
    with pytest.raises(CoverError, match="too long"):
        store.save_from_url("https://example.com/slow.png")


def test_download_network_error(tmp_path):
    def offline(request):
        raise httpx.ConnectError("geen netwerk", request=request)

    store, _ = make_store(tmp_path, offline)
    with pytest.raises(CoverError, match="reach"):
        store.save_from_url("https://example.com/cover.png")


def test_download_http_error_status(tmp_path):
    store, _ = make_store(tmp_path, lambda r: httpx.Response(404))
    with pytest.raises(CoverError, match="404"):
        store.save_from_url("https://example.com/missing.png")


def test_delete_only_touches_cover_files(tmp_path):
    covers = tmp_path / "covers"
    store, _ = make_store(covers)
    name = store.save(image_bytes())
    (tmp_path / "keep.txt").write_text("blijf")

    store.delete("../keep.txt")
    store.delete(name)

    assert not (covers / name).exists()
    assert (tmp_path / "keep.txt").exists()
