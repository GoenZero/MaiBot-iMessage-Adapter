"""能力注册表、工具可见性和能力说明。"""

from __future__ import annotations

import copy

import imessage_adapter_under_test as plugin_module
from imessage_adapter_under_test.core.capabilities import build_brief, disabled_tool_names, status_lines
from imessage_adapter_under_test.core.planner import BRIEF_ITEM_ID, adjust_planner_request


def config_with(**caps):
    plugin = plugin_module.create_plugin()
    data = plugin.build_default_config()
    data["capabilities"].update(caps)
    plugin.set_plugin_config(data)
    return plugin.config


def reply_tool():
    return {
        "type": "function",
        "function": {
            "name": "reply",
            "parameters": {
                "type": "object",
                "properties": {"msg_id": {"type": "string"}, "set_quote": {"type": "boolean", "default": True}},
                "required": ["msg_id"],
            },
        },
    }


def tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


def items():
    return [
        {"item_type": "SystemMessageItem", "meta": {"item_id": "sys", "logical_turn_id": None, "timestamp": "2026-10-11T00:00:00"}, "parts": [{"type": "text", "text": "人设"}]},
        {"item_type": "UserMessageItem", "meta": {"item_id": "u1", "logical_turn_id": None, "timestamp": "2026-10-11T00:00:01"}, "parts": [{"type": "text", "text": "你好"}]},
    ]


def kwargs():
    return {
        "items": items(),
        "item_schema_version": 1,
        "tool_definitions": [reply_tool(), tool("imessage_react"), tool("wait")],
        "selected_history_count": 1,
        "built_message_count": 2,
        "selection_reason": "",
        "session_id": "s1",
    }


def test_default_everything_on():
    config = config_with()
    assert disabled_tool_names(config) == set()
    lines = status_lines(config)
    assert all(line.startswith("✅") for line in lines if "发语音" not in line)
    # 发语音只做了预留：默认配置下也显示未开启，并说明原因
    assert "⏹ 发语音（预留，尚未接入语音合成服务）" in lines
    brief = build_brief(config)
    assert "set_quote" in brief and "quote=" in brief and "Markdown" in brief


def test_disabled_capabilities_vanish_from_brief_and_tools():
    config = config_with(reactions=False, markdown=False, reply_mode="off")
    assert disabled_tool_names(config) == {"imessage_react"}
    brief = build_brief(config)
    assert "Markdown" not in brief and "set_quote" not in brief
    assert "⏹ 点赞回应（Tapback）" in status_lines(config)
    # 引用关了要明说，否则 AI 会以为自己还能引用（真机测试发现）
    assert "引用回复已关闭" in brief
    assert "引用回复已关闭" not in build_brief(config_with())


def test_brief_is_short():
    # 目标 200 token 以内；中文大约一字一 token，留余量按 260 字符算
    assert len(build_brief(config_with())) < 260


def test_non_imessage_chat_hides_our_tools_and_adds_nothing():
    original = kwargs()
    result = adjust_planner_request(copy.deepcopy(original), is_imessage=False, hidden_tools={"imessage_react"}, reply_mode="auto", brief="")
    assert result is not None
    names = [t["function"]["name"] for t in result["tool_definitions"]]
    assert names == ["reply", "wait"]
    assert result["tool_definitions"][0] == reply_tool()  # 其他平台的 reply 工具不动
    assert result["items"] == original["items"]
    assert result["session_id"] == "s1" and result["item_schema_version"] == 1  # 其余参数原样保留


def test_imessage_auto_mode_requires_explicit_quote_choice_and_inserts_brief():
    result = adjust_planner_request(kwargs(), is_imessage=True, hidden_tools=set(), reply_mode="auto", brief="说明")
    assert result is not None
    reply = result["tool_definitions"][0]["function"]["parameters"]
    assert "set_quote" in reply["required"] and "default" not in reply["properties"]["set_quote"]
    assert [i["meta"]["item_id"] for i in result["items"]] == ["sys", BRIEF_ITEM_ID, "u1"]
    assert result["items"][1]["meta"]["timestamp"] == "2026-10-11T00:00:00"
    # 再跑一次不会重复插入
    again = adjust_planner_request(result, is_imessage=True, hidden_tools=set(), reply_mode="auto", brief="说明")
    assert again is None or [i["meta"]["item_id"] for i in again["items"]].count(BRIEF_ITEM_ID) == 1


def test_imessage_off_and_always_remove_set_quote():
    for mode in ("off", "always"):
        result = adjust_planner_request(kwargs(), is_imessage=True, hidden_tools={"imessage_react"}, reply_mode=mode, brief="")
        assert result is not None
        reply = result["tool_definitions"][0]["function"]["parameters"]
        assert "set_quote" not in reply["properties"] and "set_quote" not in reply["required"]
        assert [t["function"]["name"] for t in result["tool_definitions"]] == ["reply", "wait"]
        assert [i["meta"]["item_id"] for i in result["items"]] == ["sys", "u1"]
