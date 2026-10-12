"""图片处理：读取侧车下载的临时文件，交给 MaiBot 之前做必要的转换。

- iPhone 默认拍的是 HEIC/HEIF，MaiBot 识图读不了，统一转成 JPEG
- 太大的图片按长边缩小（默认 2048 像素）：识图用不上上千万像素，缩小能省内存和模型流量
- 按照片里记录的方向摆正
- 其他格式且尺寸不大时原样交出，不做多余的重新编码
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import base64
import io
import logging
import time

logger = logging.getLogger(__name__)

HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif")
# 不缩小时，超过这个体积也重新压缩一次，避免把十几 MB 的原图送给模型
REENCODE_ABOVE_BYTES = 4 * 1024 * 1024

_heif_registered: Optional[bool] = None


def _register_heif() -> bool:
    """按需注册 pillow-heif，让 Pillow 能打开 HEIC。只尝试一次。"""
    global _heif_registered
    if _heif_registered is None:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
            _heif_registered = True
        except ImportError:
            logger.warning("未安装 pillow-heif，HEIC 照片无法转换")
            _heif_registered = False
    return _heif_registered


def is_heif(data: bytes, mime: str) -> bool:
    if mime.lower() in ("image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"):
        return True
    return data[4:8] == b"ftyp" and data[8:12] in HEIF_BRANDS


def prepare_image(
    data: bytes, mime: str, max_edge: int, jpeg_quality: int = 85, stats: Optional[Dict[str, Any]] = None
) -> Tuple[bytes, str]:
    """把一张图片处理成适合交给 MaiBot 的样子，返回 (字节, MIME)。

    stats 不为 None 时记下像素尺寸和各步用时，供日志排查慢的原因。出错时抛异常，由调用方降级成文字说明。
    """
    from PIL import Image, ImageOps

    heif = is_heif(data, mime)
    if heif and not _register_heif():
        raise RuntimeError("缺少 pillow-heif，无法转换 HEIC 照片")
    stats = stats if stats is not None else {}
    lap = time.monotonic()

    def step(name: str) -> None:
        nonlocal lap
        now = time.monotonic()
        stats[name] = now - lap
        lap = now

    with Image.open(io.BytesIO(data)) as image:
        stats["size"] = image.size
        too_big = max_edge > 0 and max(image.size) > max_edge
        animated = bool(getattr(image, "is_animated", False))
        if not heif and (animated or (not too_big and len(data) <= REENCODE_ABOVE_BYTES)):
            # 普通尺寸的 JPEG/PNG/GIF/WebP（以及动图）原样交出
            return data, mime

        image.load()
        step("decode")
        if too_big:
            # 先缩小再摆正：缩小后只剩几百万像素，后面每一步都快得多，也少占内存
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            step("resize")
        working = ImageOps.exif_transpose(image) or image

        output = io.BytesIO()
        if working.mode in ("RGBA", "LA", "P") and not heif and mime == "image/png":
            # 带透明的 PNG 保持 PNG。压缩级别用 3：比默认的 6 快几倍，体积只大一点
            working.save(output, format="PNG", compress_level=3)
            result = output.getvalue(), "image/png"
        else:
            working.convert("RGB").save(output, format="JPEG", quality=jpeg_quality, optimize=True)
            result = output.getvalue(), "image/jpeg"
        step("encode")
        return result


def load_image_file(path: str, mime: str, *, max_edge: int = 2048) -> Tuple[Optional[str], str]:
    """读取临时图片文件并删除它，返回 (base64, MIME)；失败返回 (None, 原因)。"""
    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError as exc:
        return None, f"读取临时文件失败：{exc}"
    finally:
        file.unlink(missing_ok=True)
    if not data:
        return None, "图片是空的"
    started = time.monotonic()
    stats: Dict[str, Any] = {}
    try:
        prepared, out_mime = prepare_image(data, mime, max_edge, stats=stats)
        seconds = time.monotonic() - started
        if seconds >= 1.0:
            width, height = stats.get("size", (0, 0))
            steps = "，".join(
                f"{label} {stats[key]:.1f} 秒" for key, label in (("decode", "解码"), ("resize", "缩小"), ("encode", "编码")) if key in stats
            )
            logger.info(
                "图片处理用时 %.1f 秒（%s，%dx%d，%.1fMB → %.1fMB；%s）",
                seconds, mime, width, height, len(data) / 1048576, len(prepared) / 1048576, steps,
            )
    except Exception as exc:
        logger.warning("处理图片失败（%s，%d 字节）：%s", mime, len(data), exc)
        if is_heif(data, mime):
            return None, "HEIC 照片转换失败"
        # 其他格式转换失败时，原图交给 MaiBot 试试
        return base64.b64encode(data).decode("ascii"), mime
    return base64.b64encode(prepared).decode("ascii"), out_mime


STICKER_MAX_EDGE = 512
# 动图在这个大小以内原样当贴纸发（缩小会丢掉动画）
STICKER_KEEP_ANIMATED_BYTES = 2 * 1024 * 1024


def prepare_sticker(data: bytes) -> Tuple[bytes, str]:
    """把一张表情包处理成贴纸用的图片，返回 (字节, 文件扩展名)。

    贴纸显示得很小，静态图统一缩到 512 像素以内并存成带透明的 PNG；不太大的动图原样保留。
    出错时抛异常，由调用方告诉 AI 这次没贴成。
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        fmt = (image.format or "").upper()
        if getattr(image, "is_animated", False) and len(data) <= STICKER_KEEP_ANIMATED_BYTES and fmt in ("GIF", "PNG", "WEBP"):
            return data, {"GIF": ".gif", "PNG": ".png", "WEBP": ".webp"}[fmt]
        image.seek(0)
        working = image.convert("RGBA")
        working.thumbnail((STICKER_MAX_EDGE, STICKER_MAX_EDGE), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        working.save(output, format="PNG", compress_level=6)
        return output.getvalue(), ".png"
