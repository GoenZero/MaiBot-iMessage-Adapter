"""入站：把侧车送来的消息转换成 MaiBot 的消息字典。

规则：每个片段都要变成 MaiBot 能看懂的东西。图片交给识图；MaiBot 处理不了的
（文件、语音、名片、链接、卡片……）变成一个可读的方括号标签，绝不悄悄丢掉。

标签会和用户的话一起存进 MaiBot 的数据库，被记忆、表达学习等模块当成用户的发言读取，
所以只写中性的描述（像 MaiBot 自己的「[图片]」「[语音消息]」），不写「对方发来……」
这类旁白，更不写给 AI 的指令。该怎么应对写在能力说明里（core/capabilities.py），那里不入库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .effects import effect_name, text_effect_name

PLATFORM = "imessage"
# 文本文件交给 AI 的最大字数，超出部分截掉
MAX_TEXT_FILE_CHARS = 6000

# 图片加载器：输入（临时文件路径, MIME），返回 (base64, 说明)；失败返回 (None, 原因)
ImageLoader = Callable[[str, str], Tuple[Optional[str], str]]



def _human_size(size: Any) -> str:
    if not isinstance(size, (int, float)) or size <= 0:
        return ""
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    return f"{max(size / 1024, 0.1):.1f}KB"


def read_text_file(path: str, max_chars: int = MAX_TEXT_FILE_CHARS) -> Tuple[Optional[str], bool]:
    """读取侧车下载的文本文件并删除它，返回 (内容, 是否截断)；读不了返回 (None, False)。"""
    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError:
        return None, False
    finally:
        file.unlink(missing_ok=True)
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    if "\x00" in text:
        return None, False  # 实际是二进制文件
    truncated = len(text) > max_chars
    return (text[:max_chars] if truncated else text), truncated


def describe_text_file(part: Dict[str, Any]) -> str:
    content, truncated = read_text_file(str(part.get("path", "")))
    name = part.get("name") or "未命名"
    if content is None:
        return f"[文件：{name}（没能读取内容）]"
    tail = f"\n[文件较长，只显示了前 {MAX_TEXT_FILE_CHARS} 个字]" if truncated else ""
    return f"[文件：{name}，内容如下]\n{content.strip()}{tail}\n[文件内容结束]"


def describe_part(part: Dict[str, Any]) -> str:
    """把非图片片段变成给 AI 看的一句话。"""
    kind = part.get("type")
    if kind == "file":
        details = "，".join(x for x in (str(part.get("mime") or ""), _human_size(part.get("size"))) if x)
        text = f"[文件：{part.get('name') or '未命名'}"
        text += f"（{details}）" if details else ""
        note = part.get("note")
        if note == "text_too_large":
            text += "，太大，没有读取内容"
        elif note == "too_large":
            text += "，太大，没有接收"
        elif note == "download_failed":
            text += "，下载失败"
        return text + "]"
    if kind == "text_file":
        return describe_text_file(part)
    if kind == "voice":
        duration = part.get("duration")
        length = f"（约 {round(duration)} 秒）" if isinstance(duration, (int, float)) and duration > 0 else ""
        transcript = str(part.get("transcript") or "").strip()
        if transcript:
            return f"[语音消息{length}] {transcript}"
        if part.get("note") in ("download_failed", "transcribe_failed"):
            return f"[语音消息{length}，未能识别]"
        return f"[语音消息{length}]"
    if kind == "contact":
        fields = []
        if part.get("name"):
            fields.append(f"姓名：{part['name']}")
        if part.get("phones"):
            fields.append("电话：" + "、".join(part["phones"]))
        if part.get("emails"):
            fields.append("邮箱：" + "、".join(part["emails"]))
        if part.get("org"):
            fields.append(f"单位：{part['org']}")
        if part.get("urls"):
            fields.append("网址：" + "、".join(part["urls"]))
        return "[联系人名片] " + ("；".join(fields) if fields else "（名片里没有可读信息）")
    if kind == "link":
        return f"[链接] {part.get('url', '')}"
    if kind == "app":
        detail = " ".join(x for x in (str(part.get("caption") or ""), str(part.get("url") or "")) if x)
        return f"[应用卡片] {detail}".rstrip()
    if kind == "poll":
        options = " / ".join(part.get("options") or [])
        title = str(part.get("title") or "").strip()
        return f"[投票] {title}：{options}".rstrip("：") if title else f"[投票] {options}".rstrip()
    if kind == "effect":
        effect = str(part.get("effect", ""))
        return f"[{effect_name(effect)}特效]"
    if kind == "text_effect":
        name = text_effect_name(str(part.get("effect", "")))
        text = str(part.get("text") or "").strip()
        return f"[文字效果「{name}」：{text}]" if text else f"[文字效果「{name}」]"
    if kind == "sticker":
        return "[贴纸]"
    if kind == "unsupported":
        return f"[暂不支持的内容：{part.get('what') or '未知'}]"
    return f"[暂不支持的内容：{kind}]"


def build_raw_message(parts: List[Dict[str, Any]], load_image: ImageLoader) -> Tuple[List[Dict[str, Any]], bool]:
    """片段列表 → MaiBot raw_message。返回 (消息段列表, 是否包含图片)。"""
    segments: List[Dict[str, Any]] = []
    has_image = False

    def add_text(text: str) -> None:
        # 相邻文字合并成一段
        if segments and segments[-1]["type"] == "text":
            segments[-1]["data"] += "\n" + text
        else:
            segments.append({"type": "text", "data": text})

    for part in parts:
        kind = part.get("type")
        if kind == "text":
            text = str(part.get("text") or "")
            if text.strip():
                add_text(text)
        elif kind == "image":
            data, reason = load_image(str(part.get("path", "")), str(part.get("mime", "")))
            # 贴纸交给 MaiBot 的表情包系统（它会按表情包识别、描述），普通图片走识图
            segment_type = "emoji" if part.get("sticker") else "image"
            if data:
                segments.append({"type": segment_type, "data": "", "binary_data_base64": data, "hash": ""})
                has_image = True
            else:
                add_text(f"[{'贴纸' if part.get('sticker') else '图片'}（没能读取：{reason}）]")
        else:
            add_text(describe_part(part))

    if not segments:
        segments.append({"type": "text", "data": "[空白消息]"})
    return segments, has_image


def build_maibot_message(data: Dict[str, Any], load_image: ImageLoader) -> Dict[str, Any]:
    """侧车的 message 事件数据 → MaiBot 入站消息字典。"""
    message_id = str(data.get("message_id", ""))
    sender_id = str(data.get("sender_id", "") or "unknown")
    parts = data.get("parts") if isinstance(data.get("parts"), list) else []
    raw_message, has_image = build_raw_message(parts, load_image)

    message: Dict[str, Any] = {
        "message_id": message_id,
        "timestamp": str(float(data.get("timestamp", 0) or 0) / 1000 or ""),
        "platform": PLATFORM,
        "session_id": str(data.get("chat_id", "")),
        "message_info": {
            "user_info": {"user_id": sender_id, "user_nickname": sender_id},
            "additional_config": {},
        },
        "raw_message": raw_message,
        "is_picture": has_image,
        # 整条消息只有贴纸时，按表情包消息交给 MaiBot
        "is_emoji": bool(raw_message) and all(segment["type"] == "emoji" for segment in raw_message),
    }

    reply_to = data.get("reply_to")
    if isinstance(reply_to, dict) and reply_to.get("message_id"):
        # 引用关系：放一个 reply 段在最前面，MaiBot 会在 AI 看到的消息上标出 quote="编号"
        target_id = str(reply_to["message_id"])
        reply_data: Dict[str, Any] = {"target_message_id": target_id}
        if reply_to.get("text"):
            reply_data["target_message_content"] = str(reply_to["text"])
        message["raw_message"] = [{"type": "reply", "data": reply_data}, *raw_message]
        message["reply_to"] = target_id
    return message
