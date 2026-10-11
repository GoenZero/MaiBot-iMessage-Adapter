"""能力注册表：所有 iMessage 能力从这里出发。

每项能力一条记录：名称、对应的 Photon 调用、所需套餐、是否开启（读 WebUI 配置）、
给 AI 看的说明。规则：

- 关闭的能力既不出现在给 AI 的说明里，对应的工具也会从 AI 的工具列表里拿掉
- 依赖外部服务的能力（例如语音合成）还要「已配置」才算开启
- 只登记免费版 / Pro 可用的单聊能力；Business 才有的能力不登记
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

REPLY_MODE_TEXT = {
    "always": "总是引用",
    "auto": "AI 自己决定",
    "off": "关闭",
}


@dataclass(frozen=True)
class Capability:
    key: str
    title: str
    photon_call: str
    plan: str  # free / pro
    # 怎么接入：tool = AI 调用的工具；auto = 适配器自动完成；inbound = 收消息时自动处理
    kind: str
    is_enabled: Callable[[Any], bool]
    tool_name: Optional[str] = None
    # 写进能力说明的一句话（只写工具说明里没有的内容）；返回空串表示不需要说明
    ai_note: Callable[[Any], str] = lambda config: ""
    is_configured: Callable[[Any], bool] = lambda config: True
    # 能力关闭时写进说明的一句话：AI 容易凭常识以为自己能做，需要明说时才填
    off_note: str = ""

    def enabled(self, config: Any) -> bool:
        return bool(self.is_enabled(config)) and bool(self.is_configured(config))


def _caps(config: Any) -> Any:
    return config.capabilities


def _reply_note(config: Any) -> str:
    mode = _caps(config).reply_mode
    if mode == "auto":
        return "要针对对方某句话回应时，reply 工具的 set_quote 填 true，平时填 false。"
    if mode == "always":
        return "你的回复会自动以引用对方最新一句话的形式发出，你不需要设置 set_quote。"
    return ""


REGISTRY: Sequence[Capability] = (
    Capability(
        key="typing",
        title="输入中提示",
        photon_call="space.startTyping / stopTyping",
        plan="free",
        kind="auto",
        is_enabled=lambda c: _caps(c).typing_indicator,
    ),
    Capability(
        key="read_receipts",
        title="自动发已读",
        photon_call="message.read",
        plan="free",
        kind="auto",
        is_enabled=lambda c: _caps(c).read_receipts,
    ),
    Capability(
        key="reactions",
        title="点赞回应（Tapback）",
        photon_call="message.react",
        plan="free",
        kind="tool",
        tool_name="imessage_react",
        is_enabled=lambda c: _caps(c).reactions,
    ),
    Capability(
        key="reply",
        title="引用回复",
        photon_call="message.reply",
        plan="free",
        kind="auto",
        is_enabled=lambda c: _caps(c).reply_mode != "off",
        ai_note=_reply_note,
        off_note="引用回复已关闭：你的回复都是普通消息，不会显示引用，别说你引用了哪句。",
    ),
    Capability(
        key="markdown",
        title="Markdown 渲染",
        photon_call="markdown()",
        plan="free",
        kind="auto",
        is_enabled=lambda c: _caps(c).markdown,
        ai_note=lambda c: "可用 **粗体**、列表等 Markdown，iMessage 会显示样式；闲聊少用。",
    ),
    Capability(
        key="text_files",
        title="读取文本文件",
        photon_call="attachment.read",
        plan="free",
        kind="auto",
        is_enabled=lambda c: _caps(c).read_text_files,
        ai_note=lambda c: "对方发的 .txt、.md 等文本文件会附上内容。",
    ),
    Capability(
        key="inbound_media",
        title="接收图片（HEIC 转 JPEG）、名片、引用和组合消息",
        photon_call="app.messages",
        plan="free",
        kind="inbound",
        is_enabled=lambda c: True,
        ai_note=lambda c: (
            "对方消息带 quote=\"编号\" 表示引用了那条消息。"
            "你打不开的语音、文件等会以方括号说明出现。"
        ),
    ),
)


def enabled_capabilities(config: Any) -> List[Capability]:
    return [cap for cap in REGISTRY if cap.enabled(config)]


def disabled_tool_names(config: Any) -> set[str]:
    return {cap.tool_name for cap in REGISTRY if cap.tool_name and not cap.enabled(config)}


def all_tool_names() -> set[str]:
    return {cap.tool_name for cap in REGISTRY if cap.tool_name}


def build_brief(config: Any) -> str:
    """生成给 AI 的短说明（目标 200 token 以内）。内容只随配置变化，便于模型侧缓存。"""
    notes = [note for cap in REGISTRY if (note := cap.ai_note(config) if cap.enabled(config) else cap.off_note)]
    if not notes:
        return ""
    lines = ["[iMessage 能力说明] 你正通过 iMessage 和对方一对一私聊。"]
    lines.extend(f"- {note}" for note in notes)
    return "\n".join(lines)


def status_lines(config: Any) -> List[str]:
    """给 /imessage_status 用的能力清单。"""
    lines = []
    for cap in REGISTRY:
        if cap.kind == "inbound":
            continue
        mark = "✅" if cap.enabled(config) else "⏹"
        extra = f"（{REPLY_MODE_TEXT[_caps(config).reply_mode]}）" if cap.key == "reply" else ""
        lines.append(f"{mark} {cap.title}{extra}")
    return lines
