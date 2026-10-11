"""图片处理：读取侧车下载的临时文件，交给 MaiBot 之前做必要的转换。

- iPhone 默认拍的是 HEIC/HEIF，MaiBot 识图读不了，统一转成 JPEG
- 太大的图片按长边缩小（默认 2048 像素）：识图用不上上千万像素，缩小能省内存和模型流量
- 按照片里记录的方向摆正
- 其他格式且尺寸不大时原样交出，不做多余的重新编码
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import base64
import io
import logging

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


def prepare_image(data: bytes, mime: str, max_edge: int, jpeg_quality: int = 85) -> Tuple[bytes, str]:
    """把一张图片处理成适合交给 MaiBot 的样子，返回 (字节, MIME)。

    出错时抛异常，由调用方降级成文字说明。
    """
    from PIL import Image, ImageOps

    heif = is_heif(data, mime)
    if heif and not _register_heif():
        raise RuntimeError("缺少 pillow-heif，无法转换 HEIC 照片")

    with Image.open(io.BytesIO(data)) as image:
        too_big = max_edge > 0 and max(image.size) > max_edge
        animated = bool(getattr(image, "is_animated", False))
        if not heif and (animated or (not too_big and len(data) <= REENCODE_ABOVE_BYTES)):
            # 普通尺寸的 JPEG/PNG/GIF/WebP（以及动图）原样交出
            return data, mime

        image.load()
        working = ImageOps.exif_transpose(image) or image
        if too_big:
            working.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        output = io.BytesIO()
        if working.mode in ("RGBA", "LA", "P") and not heif and mime == "image/png":
            # 带透明的 PNG 保持 PNG
            working.save(output, format="PNG", optimize=True)
            return output.getvalue(), "image/png"
        working.convert("RGB").save(output, format="JPEG", quality=jpeg_quality, optimize=True)
        return output.getvalue(), "image/jpeg"


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
    try:
        prepared, out_mime = prepare_image(data, mime, max_edge)
    except Exception as exc:
        logger.warning("处理图片失败（%s，%d 字节）：%s", mime, len(data), exc)
        if is_heif(data, mime):
            return None, "HEIC 照片转换失败"
        # 其他格式转换失败时，原图交给 MaiBot 试试
        return base64.b64encode(data).decode("ascii"), mime
    return base64.b64encode(prepared).decode("ascii"), out_mime
