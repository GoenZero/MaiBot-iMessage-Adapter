"""HEIC 转 JPEG、大图缩小。"""

from __future__ import annotations

from pathlib import Path

import base64
import io

import pytest
from PIL import Image

from imessage_adapter_under_test.core.media import is_heif, load_image_file, prepare_image, prepare_sticker

pillow_heif = pytest.importorskip("pillow_heif")


def heic_bytes(size=(3024, 4032), exif_orientation: int | None = None) -> bytes:
    pillow_heif.register_heif_opener()
    image = Image.new("RGB", size, (200, 30, 30))
    buffer = io.BytesIO()
    kwargs = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif.tobytes()
    image.save(buffer, format="HEIF", quality=50, **kwargs)
    return buffer.getvalue()


def decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_heic_becomes_downscaled_jpeg(tmp_path: Path) -> None:
    raw = heic_bytes()
    assert is_heif(raw, "application/octet-stream")  # 只看文件头也能认出来
    path = tmp_path / "IMG_0001.HEIC"
    path.write_bytes(raw)
    encoded, mime = load_image_file(str(path), "image/heic", max_edge=2048)
    assert mime == "image/jpeg"
    assert encoded is not None
    image = decode(base64.b64decode(encoded))
    assert image.format == "JPEG"
    assert max(image.size) == 2048
    assert not path.exists()  # 临时文件用完即删


def test_heic_without_downscale_keeps_size() -> None:
    data, mime = prepare_image(heic_bytes((640, 480)), "image/heic", max_edge=0)
    assert mime == "image/jpeg"
    assert decode(data).size == (640, 480)


def test_small_png_passthrough() -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (100, 50), (0, 0, 0, 0)).save(buffer, format="PNG")
    original = buffer.getvalue()
    data, mime = prepare_image(original, "image/png", max_edge=2048)
    assert data == original and mime == "image/png"


def test_big_png_with_alpha_stays_png() -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (4000, 1000), (0, 0, 0, 0)).save(buffer, format="PNG")
    data, mime = prepare_image(buffer.getvalue(), "image/png", max_edge=2048)
    assert mime == "image/png"
    assert decode(data).size == (2048, 512)


def test_orientation_is_applied() -> None:
    # 横拍 400x200，方向标记 6（需顺时针转 90°）→ 摆正后 200x400
    raw = heic_bytes((400, 200), exif_orientation=6)
    data, _ = prepare_image(raw, "image/heic", max_edge=0)
    assert decode(data).size == (200, 400)


def test_orientation_is_applied_after_downscale() -> None:
    # 先缩小再摆正：横拍 4000x2000 + 方向 6 → 缩到 2048x1024 → 摆正成 1024x2048
    raw = heic_bytes((4000, 2000), exif_orientation=6)
    stats: dict = {}
    data, _ = prepare_image(raw, "image/heic", max_edge=2048, stats=stats)
    assert decode(data).size == (1024, 2048)
    assert sorted(stats["size"]) == [2000, 4000] and {"decode", "resize", "encode"} <= stats.keys()


def test_big_jpeg_orientation_after_downscale() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    Image.new("RGB", (5000, 2500), (10, 120, 200)).save(buffer, format="JPEG", exif=exif.tobytes())
    data, mime = prepare_image(buffer.getvalue(), "image/jpeg", max_edge=2048)
    assert mime == "image/jpeg"
    assert decode(data).size == (1024, 2048)


def test_broken_heic_reports_failure(tmp_path: Path) -> None:
    path = tmp_path / "bad.heic"
    path.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
    encoded, reason = load_image_file(str(path), "image/heic")
    assert encoded is None and "HEIC" in reason


def test_unknown_but_broken_non_heic_is_passed_through(tmp_path: Path) -> None:
    path = tmp_path / "weird.jpg"
    path.write_bytes(b"not really a jpeg")
    encoded, mime = load_image_file(str(path), "image/jpeg")
    assert base64.b64decode(encoded or "") == b"not really a jpeg" and mime == "image/jpeg"



def test_sticker_is_small_png_and_animated_gif_is_kept() -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (1200, 800), (255, 0, 0, 128)).save(buffer, format="PNG")
    data, ext = prepare_sticker(buffer.getvalue())
    image = decode(data)
    assert ext == ".png" and image.format == "PNG" and image.mode == "RGBA" and max(image.size) == 512

    frames = [Image.new("RGB", (64, 64), color) for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    gif = io.BytesIO()
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    kept, ext = prepare_sticker(gif.getvalue())
    assert ext == ".gif" and kept == gif.getvalue()  # 动图原样保留



def test_transparent_heic_sticker_stays_transparent() -> None:
    # Memoji、照片抠出来的贴纸是带透明的 HEIC：转成 PNG，不能变成黑底 JPEG
    pillow_heif.register_heif_opener()
    buffer = io.BytesIO()
    Image.new("RGBA", (300, 300), (255, 0, 0, 0)).save(buffer, format="HEIF", quality=80)
    data, mime = prepare_image(buffer.getvalue(), "image/heic", max_edge=2048)
    image = decode(data)
    assert mime == "image/png" and image.mode == "RGBA"
    assert image.getpixel((10, 10))[3] == 0
