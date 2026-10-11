"""入站：把侧车送来的消息转换成 MaiBot 的消息字典。

规则：每个片段都要变成 MaiBot 能看懂的东西。图片交给识图；MaiBot 处理不了的
（文件、语音、名片、链接、卡片……）变成一句可读的说明，绝不悄悄丢掉。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PLATFORM = "imessage"
# 文本文件交给 AI 的最大字数，超出部分截掉
MAX_TEXT_FILE_CHARS = 6000

# 图片加载器：输入（临时文件路径, MIME），返回 (base64, 说明)；失败返回 (None, 原因)
ImageLoader = Callable[[str, str], Tuple[Optional[str], str]]

EFFECT_NAMES: Dict[str, str] = {
    "com.apple.MobileSMS.expressivesend.impact": "重击",
    "com.apple.MobileSMS.expressivesend.loud": "放大",
    "com.apple.MobileSMS.expressivesend.gentle": "轻柔",
    "com.apple.MobileSMS.expressivesend.invisibleink": "隐形墨水",
    "com.apple.messages.effect.CKConfettiEffect": "五彩纸屑",
    "com.apple.messages.effect.CKFireworksEffect": "烟花",
    "com.apple.messages.effect.CKBalloonEffect": "气球",
    "com.apple.messages.effect.CKHeartEffect": "爱心",
    "com.apple.messages.effect.CKLasersEffect": "激光",
    "com.apple.messages.effect.CKHappyBirthdayEffect": "庆祝",
    "com.apple.messages.effect.CKSparklesEffect": "闪光",
    "com.apple.messages.effect.CKSpotlightEffect": "聚光灯",
    "com.apple.messages.effect.CKEchoEffect": "回声",
}


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
        return f"[对方发来一个文件：{name}，但没能读取内容]"
    tail = f"\n[文件较长，只显示了前 {MAX_TEXT_FILE_CHARS} 个字]" if truncated else ""
    return f"[对方发来一个文件：{name}，内容如下]\n{content.strip()}{tail}\n[文件内容结束]"


def describe_part(part: Dict[str, Any]) -> str:
    """把非图片片段变成给 AI 看的一句话。"""
    kind = part.get("type")
    if kind == "file":
        details = "，".join(x for x in (str(part.get("mime") or ""), _human_size(part.get("size"))) if x)
        text = f"[对方发来一个文件：{part.get('name') or '未命名'}"
        text += f"（{details}）" if details else ""
        note = part.get("note")
        if note == "text_too_large":
            text += "，文件太大，没有读取内容"
        elif note == "too_large":
            text += "，文件太大，没有接收"
        elif note == "download_failed":
            text += "，下载失败，没能打开"
        else:
            text += "，你暂时打不开这类文件"
        return text + "]"
    if kind == "text_file":
        return describe_text_file(part)
    if kind == "voice":
        duration = part.get("duration")
        length = f"，约 {round(duration)} 秒" if isinstance(duration, (int, float)) and duration > 0 else ""
        return f"[对方发来一条语音消息{length}，你暂时听不了语音]"
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
        return "[对方发来一张联系人名片] " + ("；".join(fields) if fields else "（名片里没有可读信息）")
    if kind == "link":
        return f"[链接] {part.get('url', '')}"
    if kind == "app":
        detail = " ".join(x for x in (str(part.get("caption") or ""), str(part.get("url") or "")) if x)
        return f"[对方发来一张应用卡片] {detail}".rstrip()
    if kind == "poll":
        options = " / ".join(part.get("options") or [])
        return f"[对方发起了一个投票] {part.get('title', '')}：{options}".rstrip("：")
    if kind == "effect":
        effect = str(part.get("effect", ""))
        return f"（这条消息带有「{EFFECT_NAMES.get(effect, effect)}」特效）"
    if kind == "unsupported":
        return f"[对方发了一条暂不支持的内容：{part.get('what') or '未知'}]"
    return f"[对方发了一条暂不支持的内容：{kind}]"


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
            if data:
                segments.append({"type": "image", "data": "", "binary_data_base64": data, "hash": ""})
                has_image = True
            else:
                add_text(f"[对方发来一张图片，但没能读取：{reason}]")
        else:
            add_text(describe_part(part))

    if not segments:
        segments.append({"type": "text", "data": "[对方发了一条空白消息]"})
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
