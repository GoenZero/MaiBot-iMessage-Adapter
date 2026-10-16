"""出站：把 MaiBot 交给网关的消息字典转换成侧车的 send 请求。

MaiBot 交来的 ``raw_message`` 是消息段列表（实测格式见 docs/AUDIT.md 第 5 题）：
- ``text``：文字
- ``image`` / ``emoji``：图片，字节在 ``binary_data_base64`` 里
- ``reply``：引用回复，``data.target_message_id`` 是被引用消息的编号
- ``voice``：语音（发语音暂未开放，见能力注册表）
- ``dict``：插件发来的自定义内容。本插件的特效、投票工具用它做标记：
  ``{"type": "dict", "data": {"type": "烟花特效", "imessage": {"effect": "fireworks"}}}``。
  外层的 ``type`` 是给 AI 看的名字（聊天记录里显示成「[烟花特效消息]」），``imessage`` 里才是动作
图片字节写成临时文件交给侧车，管道里只传路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import base64
import binascii
import io
import re
import secrets


@dataclass
class OutboundPlan:
    """一次发送要做的事。"""

    parts: List[Dict[str, Any]] = field(default_factory=list)
    reply_to: Optional[str] = None
    temp_files: List[Path] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    # 消息特效（imessage_send_effect 工具），加在第一段文字上
    effect: Optional[str] = None

    def cleanup(self) -> None:
        for path in self.temp_files:
            path.unlink(missing_ok=True)
        self.temp_files.clear()


_MARKDOWN_PATTERN = re.compile(
    r"(\*\*[^*\n]+\*\*)"  # **粗体**
    r"|(__[^_\n]+__)"  # __粗体__
    r"|(~~[^~\n]+~~)"  # ~~删除线~~
    r"|(`[^`\n]+`)"  # `代码`
    r"|(\[[^\]\n]+\]\(https?://[^)\s]+\))"  # [文字](链接)
    r"|(^\s{0,3}#{1,6}\s+\S)"  # # 标题
    r"|(^\s{0,3}[-*+]\s+\S.*\n\s{0,3}[-*+]\s+\S)"  # 至少两行的列表
    r"|(^\s{0,3}\d+[.)]\s+\S.*\n\s{0,3}\d+[.)]\s+\S)",  # 至少两行的编号列表
    re.MULTILINE,
)


def looks_like_markdown(text: str) -> bool:
    """文字里有明确的 Markdown 语法才按 Markdown 渲染；普通聊天文字（含单个星号、单行「- 」）不受影响。"""
    return bool(_MARKDOWN_PATTERN.search(text))


def sniff_image_mime(data: bytes) -> str:
    """按文件头判断图片格式（MaiBot 不告诉我们格式）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1"):
        return "image/heic"
    return "application/octet-stream"


_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/heic": ".heic"}


def _convert_to_png(data: bytes) -> Optional[bytes]:
    """把认不出格式的图片（BMP、TIFF 等）转成 PNG；Pillow 也打不开时返回 None。"""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            frame = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P", "PA") else "RGB")
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG", compress_level=3)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - 任何解析失败都按「转不了」处理
        return None


def _write_temp(media_dir: Path, data: bytes, mime: str) -> Path:
    path = media_dir / f"out-{secrets.token_hex(8)}{_EXTENSIONS.get(mime, '.bin')}"
    path.write_bytes(data)
    return path


def build_outbound_plan(message: Dict[str, Any], media_dir: Path, max_bytes: int) -> OutboundPlan:
    """解析 MaiBot 出站消息字典。图片会写成临时文件，调用方发送后要 cleanup()。"""
    plan = OutboundPlan()
    raw_message = message.get("raw_message")
    segments = raw_message if isinstance(raw_message, list) else []

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        kind = str(segment.get("type", "")).strip().lower()
        if kind == "text":
            text = str(segment.get("data") or "")
            if not text:
                continue
            if plan.parts and plan.parts[-1]["type"] == "text":
                plan.parts[-1]["text"] += text
            else:
                plan.parts.append({"type": "text", "text": text})
        elif kind in ("image", "emoji"):
            encoded = segment.get("binary_data_base64")
            if not encoded:
                plan.skipped.append(f"{kind}（没有图片数据）")
                continue
            try:
                data = base64.b64decode(str(encoded), validate=False)
            except (binascii.Error, ValueError):
                plan.skipped.append(f"{kind}（图片数据损坏）")
                continue
            if len(data) > max_bytes:
                plan.skipped.append(f"{kind}（{len(data) // 1024 // 1024}MB，超过附件上限）")
                continue
            mime = sniff_image_mime(data)
            if mime not in _EXTENSIONS:
                # 认不出的格式原样发出去，iPhone 上会显示成一个叫 attachment 的文件（真机测试发现），先转成 PNG
                converted = _convert_to_png(data)
                if converted is None:
                    plan.skipped.append(f"{kind}（认不出的图片格式）")
                    continue
                data, mime = converted, "image/png"
            path = _write_temp(media_dir, data, mime)
            plan.temp_files.append(path)
            # 带上文件名和扩展名，iPhone 才会按图片显示
            plan.parts.append({"type": "file", "path": str(path), "mime": mime, "name": f"image{_EXTENSIONS[mime]}"})
        elif kind == "reply":
            data = segment.get("data")
            target = data.get("target_message_id") if isinstance(data, dict) else data
            if target and plan.reply_to is None:
                plan.reply_to = str(target)
        elif kind == "at":
            # 一对一私聊里 @ 没有意义，直接略过
            continue
        elif kind == "dict":
            _apply_imessage_marker(plan, segment.get("data"))
        else:
            plan.skipped.append(kind or "未知段")

    if not plan.parts:
        fallback = str(message.get("processed_plain_text") or "").strip()
        if fallback:
            plan.parts.append({"type": "text", "text": fallback})
    if plan.effect:
        text_part = next((part for part in plan.parts if part["type"] == "text"), None)
        if text_part is None:
            plan.skipped.append(f"特效 {plan.effect}（没有文字可加）")
        else:
            text_part["effect"] = plan.effect
    return plan


def _apply_imessage_marker(plan: OutboundPlan, data: Any) -> None:
    """处理本插件工具放进消息里的 iMessage 动作标记；其他插件的自定义内容记为跳过。"""
    action = data.get("imessage") if isinstance(data, dict) else None
    if not isinstance(action, dict):
        label = data.get("type") if isinstance(data, dict) else None
        plan.skipped.append(f"自定义内容（{label or '未知'}）")
        return
    if isinstance(action.get("effect"), str):
        plan.effect = action["effect"]
    elif isinstance(action.get("poll"), dict):
        poll = action["poll"]
        options = [str(option) for option in poll.get("options", []) if str(option).strip()]
        plan.parts.append({"type": "poll", "title": str(poll.get("title") or ""), "options": options})
    else:
        plan.skipped.append("未知的 iMessage 动作")


_BARE_URL = re.compile(r"https?://[^\s<>\"']+")


def apply_link_previews(parts: List[Dict[str, Any]]) -> None:
    """一段文字整段就是一个链接时，改用链接预览卡片发送（iMessage 会显示网页标题和图片）。"""
    for index, part in enumerate(parts):
        if part["type"] != "text" or part.get("effect") or part.get("markdown"):
            continue
        text = part["text"].strip()
        if _BARE_URL.fullmatch(text):
            parts[index] = {"type": "link", "url": text}


def resolve_target(message: Dict[str, Any]) -> str:
    """找出要发给谁：MaiBot 在私聊出站消息里总会带上对方的用户 ID（手机号或邮箱）。

    注意 ``message_info.user_info`` 是机器人自己，不能拿来当收件人。
    """
    message_info = message.get("message_info")
    additional = message_info.get("additional_config", {}) if isinstance(message_info, dict) else {}
    if not isinstance(additional, dict):
        return ""
    return str(additional.get("platform_io_target_user_id", "") or "").strip()
