"""改写 Maisaka 规划器请求（maisaka.planner.before_request 钩子）。

做三件事：
1. 不是 iMessage 的聊天：拿掉本插件的所有工具（QQ 等其他平台的聊天看不到 imessage_*）
2. iMessage 聊天：拿掉已关闭能力对应的工具；按引用档位改写内置 reply 工具的 set_quote 参数
3. iMessage 聊天：在人设（第一条系统消息）后面插入一段由能力注册表生成的短说明

实测（MaiBot 1.3.5）：在 items 里插入一条 SystemMessageItem 可以被正常还原；
返回的 modified_kwargs 会整体替换原参数，所以必须带上全部原有键。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import copy

BRIEF_ITEM_ID = "imessage-capability-brief"

SET_QUOTE_AUTO_DESCRIPTION = (
    "这是 iMessage 一对一私聊。true：把回复以「引用对方那条消息」的形式发出，"
    "只在需要明确针对对方某一句话（对方连发好几句、或在回应较早的话）时使用；"
    "false：普通回复，大多数时候用这个。必须明确填写。"
)


def tool_name(definition: Dict[str, Any]) -> str:
    function = definition.get("function")
    if isinstance(function, dict):
        return str(function.get("name", ""))
    return str(definition.get("name", ""))


def reply_tool_has_set_quote(tool_definitions: Any) -> bool:
    """MaiBot 的 reply 工具还让 AI 选「要不要引用」吗。

    MaiBot 1.3.10 起去掉了 set_quote 参数，改由它自己的「私聊引用频率」设置决定引用；
    这时就不该再提示 AI 去填这个参数。拿不到工具列表时按旧版处理。
    """
    if not isinstance(tool_definitions, list):
        return True
    for definition in tool_definitions:
        if isinstance(definition, dict) and tool_name(definition) == "reply":
            function = definition.get("function") if isinstance(definition.get("function"), dict) else definition
            parameters = function.get("parameters") if isinstance(function.get("parameters"), dict) else {}
            properties = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else {}
            return "set_quote" in properties
    return True


def _rewrite_reply_tool(definition: Dict[str, Any], reply_mode: str) -> Dict[str, Any]:
    """按引用档位改写内置 reply 工具的 set_quote 参数。"""
    rewritten = copy.deepcopy(definition)
    function = rewritten.get("function") if isinstance(rewritten.get("function"), dict) else rewritten
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        return definition
    properties = parameters.get("properties")
    if not isinstance(properties, dict) or "set_quote" not in properties:
        return definition
    required = [name for name in parameters.get("required", []) if name != "set_quote"]
    if reply_mode == "auto":
        # MaiBot 在 AI 没填时按 true 处理，私聊里会变成句句引用；这里要求 AI 必须明确选择
        properties["set_quote"] = {"type": "boolean", "description": SET_QUOTE_AUTO_DESCRIPTION}
        required.append("set_quote")
    else:
        # always / off：由适配器决定，AI 不需要这个参数
        properties.pop("set_quote")
    parameters["required"] = required
    return rewritten


def _insert_brief(items: List[Dict[str, Any]], brief: str) -> List[Dict[str, Any]]:
    result = [item for item in items if not (isinstance(item, dict) and item.get("meta", {}).get("item_id") == BRIEF_ITEM_ID)]
    if not brief:
        return result
    index = next((i for i, item in enumerate(result) if isinstance(item, dict) and item.get("item_type") == "SystemMessageItem"), None)
    anchor = result[index] if index is not None else None
    timestamp = anchor.get("meta", {}).get("timestamp") if anchor else None
    brief_item = {
        "item_type": "SystemMessageItem",
        "meta": {"item_id": BRIEF_ITEM_ID, "logical_turn_id": None, "timestamp": timestamp or "2000-01-01T00:00:00"},
        "parts": [{"type": "text", "text": brief}],
    }
    position = index + 1 if index is not None else 0
    return [*result[:position], brief_item, *result[position:]]


def adjust_planner_request(
    kwargs: Dict[str, Any],
    *,
    is_imessage: bool,
    hidden_tools: Iterable[str],
    reply_mode: str,
    brief: str,
) -> Optional[Dict[str, Any]]:
    """返回改写后的完整参数；不需要改动时返回 None。"""
    hidden = set(hidden_tools)
    raw_tools = kwargs.get("tool_definitions")
    tools = [t for t in raw_tools if isinstance(t, dict)] if isinstance(raw_tools, list) else None
    raw_items = kwargs.get("items")
    items = list(raw_items) if isinstance(raw_items, list) else None

    new_tools = tools
    if tools is not None:
        new_tools = [t for t in tools if tool_name(t) not in hidden]
        if is_imessage:
            new_tools = [_rewrite_reply_tool(t, reply_mode) if tool_name(t) == "reply" else t for t in new_tools]

    new_items = items
    if items is not None and is_imessage:
        new_items = _insert_brief(items, brief)

    if new_tools == tools and new_items == items:
        return None
    modified = dict(kwargs)
    if new_tools is not None:
        modified["tool_definitions"] = new_tools
    if new_items is not None:
        modified["items"] = new_items
    return modified
