"""图片处理：读取侧车下载的临时文件，交给 MaiBot 之前做必要的转换。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import base64


def load_image_file(path: str, mime: str) -> Tuple[Optional[str], str]:
    """读取临时图片文件并删除它，返回 (base64, 说明)；失败返回 (None, 原因)。"""
    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError as exc:
        return None, f"读取临时文件失败：{exc}"
    finally:
        file.unlink(missing_ok=True)
    if not data:
        return None, "图片是空的"
    return base64.b64encode(data).decode("ascii"), mime
