"""对方的点赞、投票选择、已读、编辑、撤回 → 给 AI 看的一句系统通知。

通知用 <imessage_event> 包起来，和 MaiBot 自己的插件通知（<plugin_proactive_task>）写法一致，
让 AI 分得清这是系统通知，不是对方说的话。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

TAPBACK_TEXT = {"❤": "爱心", "👍": "赞", "👎": "踩", "😂": "哈哈", "‼": "感叹号", "❓": "问号"}
PREVIEW_LIMIT = 30


def _clip(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= PREVIEW_LIMIT else text[:PREVIEW_LIMIT] + "…"


def wrap_event(text: str) -> str:
    return f"<imessage_event>{text}</imessage_event>"


def describe_control_event(event: Dict[str, Any]) -> Optional[str]:
    """返回给 AI 看的通知；不需要告诉 AI 的事件返回 None。"""
    kind = event.get("type")
    if kind == "reaction":
        emoji = str(event.get("emoji") or "")
        # ❤️ 和 ‼️ 带不带「变体选择符」都算同一个
        name = TAPBACK_TEXT.get(emoji.replace("\ufe0f", ""))
        label = f"「{name}」" if name else (f"「{emoji}」" if emoji else "一个")
        from_me = event.get("target_from_me")
        whose = "你的消息" if from_me is True else ("自己发的消息" if from_me is False else "一条消息")
        preview = _clip(event.get("target_text"))
        target = f"{whose}「{preview}」" if preview else whose
        return wrap_event(f"对方给{target}点了{label}回应")
    if kind == "poll_vote":
        title = _clip(event.get("poll_title"))
        option = _clip(event.get("option")) or "（未知选项）"
        verb = "选了" if event.get("selected", True) else "取消了"
        # 不是 AI 发起的投票，Photon 不给标题
        where = f"投票「{title}」里" if title else "投票里"
        return wrap_event(f"对方在{where}{verb}「{option}」")
    if kind == "read":
        return wrap_event("对方已读了你发的消息")
    if kind == "edited":
        new_text = _clip(event.get("text"))
        old_text = _clip(event.get("old_text"))
        if old_text:
            return wrap_event(f"对方把消息「{old_text}」改成了「{new_text}」")
        return wrap_event(f"对方修改了一条消息，改成了「{new_text}」")
    if kind == "unsent":
        old_text = _clip(event.get("old_text"))
        return wrap_event(f"对方撤回了消息「{old_text}」" if old_text else "对方撤回了一条消息")
    if kind == "reaction_removed":
        emoji = str(event.get("emoji") or "")
        name = TAPBACK_TEXT.get(emoji.replace("\ufe0f", ""))
        label = f"「{name or emoji}」" if (name or emoji) else ""
        from_me = event.get("target_from_me")
        whose = "你的消息" if from_me is True else "一条消息"
        preview = _clip(event.get("target_text"))
        target = f"{whose}「{preview}」" if preview else whose
        return wrap_event(f"对方取消了给{target}点的{label}回应")
    return None
