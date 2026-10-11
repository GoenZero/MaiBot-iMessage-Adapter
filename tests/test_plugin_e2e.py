"""端到端：把整个插件放进一个假的 MaiBot 环境里，用假侧车跑一遍收和发。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncio
import copy
import os
import shutil

import pytest
from maibot_sdk.context import PluginContext, PluginPaths

import imessage_adapter_under_test as plugin_module
from imessage_adapter_under_test.core import node_runtime

FAKE = Path(__file__).with_name("fake_sidecar.mjs")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="需要 node")


class FakeHost:
    """记录插件调用的 Host 方法。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def rpc(self, method: str, plugin_id: str, payload: dict[str, Any], timeout_ms: int | None = None) -> Any:
        self.calls.append((method, payload))
        if method == "host.route_message":
            return {"accepted": True}
        if method == "host.update_message_gateway_state":
            return {"accepted": True}
        if method == "cap.call":
            if payload["capability"] == "chat.get_all_streams":
                return {"success": True, "streams": [{"session_id": "s-imessage", "user_id": "+1", "is_group_session": False}]}
            return {"success": True}
        return {}

    def of(self, method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == method]


def make_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, emit: bool) -> tuple[Any, FakeHost]:
    host = FakeHost()
    plugin = plugin_module.create_plugin()
    paths = PluginPaths(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime")
    plugin._set_context(PluginContext("com.galeros.imessage_adapter", host.rpc, paths))
    plugin.set_plugin_config(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "photon": {"project_id": "pid", "project_secret": "TOPSECRET"},
            "bridge": {"retry_interval": 1.0},
        }
    )
    monkeypatch.setattr(plugin_module, "SIDECAR_SCRIPT", FAKE)
    monkeypatch.setenv("FAKE_EMIT", "1" if emit else "0")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "ops.jsonl"))

    async def fake_resolve(configured: str, logger: Any) -> tuple[str, str]:
        return NODE, "vtest"

    monkeypatch.setattr(plugin_module, "resolve_node", fake_resolve)
    return plugin, host


def sidecar_ops(tmp_path: Path) -> list[dict[str, Any]]:
    log = tmp_path / "ops.jsonl"
    if not log.exists():
        return []
    import json

    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


async def wait_for(predicate, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("等待超时")
        await asyncio.sleep(0.05)


def test_inbound_and_outbound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=True)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: len(host.of("host.route_message")) == 3)
        routed = host.of("host.route_message")
        assert [r["external_message_id"] for r in routed] == ["m1", "m2", "m3"]
        assert routed[0]["message"]["raw_message"] == [{"type": "text", "data": "第1条"}]
        assert host.of("host.update_message_gateway_state")[-1]["ready"] is True

        outbound = {
            "message_info": {"user_info": {"user_id": "+BOT"}, "additional_config": {"platform_io_target_user_id": "+1"}},
            "raw_message": [
                {"type": "reply", "data": {"target_message_id": "m3"}},
                {"type": "text", "data": "收到"},
            ],
        }
        result = await plugin.send_to_imessage(outbound)
        assert result["success"] is True
        assert str(result["external_message_id"]).startswith("out-")

        missing = await plugin.send_to_imessage({"raw_message": [{"type": "text", "data": "x"}]})
        assert missing == {"success": False, "error": "缺少目标用户"}

        await plugin.on_unload()
        assert host.of("host.update_message_gateway_state")[-1]["ready"] is False

    asyncio.run(main())


def test_disabled_plugin_stays_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
    plugin.set_plugin_config({"plugin": {"enabled": False, "config_version": "2.0.0"}, "photon": {"project_id": "p", "project_secret": "s"}})

    async def main() -> None:
        await plugin.on_load()
        result = await plugin.send_to_imessage({"raw_message": []})
        assert result == {"success": False, "error": "iMessage 网关未就绪"}
        await plugin.on_unload()

    asyncio.run(main())


def test_status_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        await plugin.handle_status(stream_id="s1")
        await plugin.on_unload()

    asyncio.run(main())
    sent = [p for p in host.of("cap.call") if p["capability"] == "send.text"]
    text = sent[-1]["args"]["text"]
    assert "✅ 已连接" in text and "TOPSECRET" not in text


def test_node_probe_reports_missing_exec_bit(tmp_path: Path) -> None:
    fake_node = tmp_path / "node"
    fake_node.write_text("#!/bin/sh\necho v24.0.0\n")
    os.chmod(fake_node, 0o644)  # 模拟 fnOS 上丢失可执行位

    async def main() -> None:
        major, detail = await node_runtime.probe_node_version(str(fake_node))
        assert major is None and "可执行权限" in detail
        os.chmod(fake_node, 0o755)
        major, detail = await node_runtime.probe_node_version(str(fake_node))
        assert major == 24

    asyncio.run(main())


def test_planner_hook_distinguishes_imessage_chats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
    tools = [
        {"type": "function", "function": {"name": "reply", "parameters": {"type": "object", "properties": {"set_quote": {"type": "boolean"}}}}},
        {"type": "function", "function": {"name": "imessage_react", "parameters": {"type": "object", "properties": {}}}},
    ]
    items = [{"item_type": "SystemMessageItem", "meta": {"item_id": "sys", "logical_turn_id": None, "timestamp": "2026-10-11T00:00:00"}, "parts": [{"type": "text", "text": "人设"}]}]

    async def main() -> None:
        await plugin.on_load()
        imessage = await plugin.on_planner_request(items=items, tool_definitions=tools, session_id="s-imessage", item_schema_version=1)
        assert imessage is not None
        modified = imessage["modified_kwargs"]
        assert [t["function"]["name"] for t in modified["tool_definitions"]] == ["reply", "imessage_react"]
        assert "[iMessage 能力说明]" in modified["items"][1]["parts"][0]["text"]

        qq = await plugin.on_planner_request(items=items, tool_definitions=tools, session_id="s-qq", item_schema_version=1)
        assert qq is not None
        assert [t["function"]["name"] for t in qq["modified_kwargs"]["tool_definitions"]] == ["reply"]
        assert qq["modified_kwargs"]["items"] == items
        await plugin.on_unload()

    asyncio.run(main())


def test_react_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        ok = await plugin.tool_react(msg_id="m1", kind="love", stream_id="s-imessage")
        assert ok == {"success": True, "content": "已给这条消息点了「爱心」"}
        missing = await plugin.tool_react(msg_id="missing", kind="like", stream_id="s-imessage")
        assert missing["success"] is False and "找不到这条消息" in missing["error"]
        wrong_kind = await plugin.tool_react(msg_id="m1", kind="angry", stream_id="s-imessage")
        assert wrong_kind["success"] is False
        not_imessage = await plugin.tool_react(msg_id="m1", kind="love", stream_id="s-qq")
        assert not_imessage["success"] is False and "不是 iMessage" in not_imessage["error"]
        await plugin.on_unload()

    asyncio.run(main())
    reacts = [op for op in sidecar_ops(tmp_path) if op["op"] == "react"]
    assert reacts[0]["args"] == {"chat_id": "+1", "message_id": "m1", "kind": "love"}
    assert len(reacts) == 2


def test_read_receipts_follow_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=True)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: len([op for op in sidecar_ops(tmp_path) if op["op"] == "mark_read"]) == 3)
        await plugin.on_unload()

    asyncio.run(main())
    reads = [op["args"] for op in sidecar_ops(tmp_path) if op["op"] == "mark_read"]
    assert reads[0] == {"chat_id": "any;-;+1", "message_id": "m1"}

    (tmp_path / "ops.jsonl").unlink()
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=True)
    data = plugin.get_plugin_config_data()
    data["capabilities"]["read_receipts"] = False
    plugin.set_plugin_config(data)

    async def main_off() -> None:
        await plugin.on_load()
        await wait_for(lambda: len(host.of("host.route_message")) == 3)
        await asyncio.sleep(0.3)
        await plugin.on_unload()

    asyncio.run(main_off())
    assert not [op for op in sidecar_ops(tmp_path) if op["op"] == "mark_read"]


def test_typing_indicator_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
    outbound = {
        "message_info": {"additional_config": {"platform_io_target_user_id": "+1"}},
        "raw_message": [{"type": "text", "data": "好"}],
    }

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        await plugin.on_replyer_request(session_id="s-qq")  # 别的平台：不显示
        await plugin.on_replyer_request(session_id="s-imessage")
        result = await plugin.send_to_imessage(outbound)
        assert result["success"] is True
        await wait_for(lambda: [op["op"] for op in sidecar_ops(tmp_path)][-1:] == ["typing"] and len(sidecar_ops(tmp_path)) >= 3)
        await plugin.on_unload()

    asyncio.run(main())
    ops = [(op["op"], op["args"].get("state")) for op in sidecar_ops(tmp_path) if op["op"] in ("typing", "send")]
    assert ops == [("typing", "start"), ("send", None), ("typing", "stop")]
    assert [op["args"]["chat_id"] for op in sidecar_ops(tmp_path) if op["op"] == "typing"] == ["+1", "+1"]


def _reply_mode_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> list[dict[str, Any]]:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=True)
    data = plugin.get_plugin_config_data()
    data["capabilities"]["reply_mode"] = mode
    plugin.set_plugin_config(data)

    def outbound(with_reply: bool) -> dict[str, Any]:
        segments: list[dict[str, Any]] = [{"type": "text", "data": "好"}]
        if with_reply:
            segments.insert(0, {"type": "reply", "data": {"target_message_id": "m1"}})
        return {"message_info": {"additional_config": {"platform_io_target_user_id": "+1"}}, "raw_message": segments}

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: len(host.of("host.route_message")) == 3)
        assert (await plugin.send_to_imessage(outbound(True)))["success"]
        assert (await plugin.send_to_imessage(outbound(False)))["success"]
        assert (await plugin.send_to_imessage(outbound(False)))["success"]
        await plugin.on_unload()

    asyncio.run(main())
    return [op["args"] for op in sidecar_ops(tmp_path) if op["op"] == "send"]


def test_reply_mode_auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sends = _reply_mode_run(tmp_path, monkeypatch, "auto")
    assert [s.get("reply_to") for s in sends] == ["m1", None, None]
    assert sends[0]["chat_id"] == "any;-;+1"  # 用入站时记下的对话编号


def test_reply_mode_always(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sends = _reply_mode_run(tmp_path, monkeypatch, "always")
    # 第一条按 MaiBot 指定的；第二条自动引用对方最新一条（m3）；第三条不再重复引用 m3
    assert [s.get("reply_to") for s in sends] == ["m1", "m3", None]


def test_reply_mode_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sends = _reply_mode_run(tmp_path, monkeypatch, "off")
    assert [s.get("reply_to") for s in sends] == [None, None, None]


def test_markdown_flag_follows_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(enabled: bool) -> list[dict[str, Any]]:
        (tmp_path / "ops.jsonl").unlink(missing_ok=True)
        plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
        data = plugin.get_plugin_config_data()
        data["capabilities"]["markdown"] = enabled
        plugin.set_plugin_config(data)
        message = {
            "message_info": {"additional_config": {"platform_io_target_user_id": "+1"}},
            "raw_message": [{"type": "text", "data": "记住**这个**"}],
        }

        async def main() -> None:
            await plugin.on_load()
            await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
            assert (await plugin.send_to_imessage(message))["success"]
            await plugin.on_unload()

        asyncio.run(main())
        return [op["args"]["parts"][0] for op in sidecar_ops(tmp_path) if op["op"] == "send"]

    assert run(True) == [{"type": "text", "text": "记住**这个**", "markdown": True}]
    assert run(False) == [{"type": "text", "text": "记住**这个**"}]


def test_bot_number_is_reported_as_gateway_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
    data = plugin.get_plugin_config_data()
    data["photon"]["bot_number"] = "+1 (415) 555-0100"
    plugin.set_plugin_config(data)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: any(p["ready"] for p in host.of("host.update_message_gateway_state")))
        await plugin.on_unload()

    asyncio.run(main())
    ready = [p for p in host.of("host.update_message_gateway_state") if p["ready"]]
    assert ready[0]["account_id"] == "+14155550100"
    assert ready[0]["platform"] == "imessage"


def test_normalize_account() -> None:
    assert plugin_module.normalize_account(" +86 138-0000-0000 ") == "+8613800000000"
    assert plugin_module.normalize_account("Bot@iCloud.com") == "bot@icloud.com"
    assert plugin_module.normalize_account("") == ""


def test_capability_toggle_does_not_restart_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        supervisor = plugin._supervisor
        pid = supervisor.status.pid
        data = copy.deepcopy(plugin.get_plugin_config_data())
        data["capabilities"]["read_receipts"] = False
        data["capabilities"]["reply_mode"] = "off"
        await plugin.on_config_update("self", data, "")
        assert plugin._supervisor is supervisor and supervisor.status.pid == pid  # 没重启
        assert plugin.config.capabilities.read_receipts is False  # 但新配置已生效

        data = copy.deepcopy(plugin.get_plugin_config_data())
        data["capabilities"]["read_text_files"] = False  # 这一项由侧车决定，需要重启
        await plugin.on_config_update("self", data, "")
        assert plugin._supervisor is not supervisor
        await plugin.on_unload()

    asyncio.run(main())


def test_quote_off_hook_turns_off_set_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
    kwargs = {"message": {"platform": "imessage", "message_id": "x"}, "set_reply": True, "typing": False, "reply_message_id": "m1"}

    assert asyncio.run(plugin.on_before_send(**kwargs)) is None  # auto 档不动
    data = plugin.get_plugin_config_data()
    data["capabilities"]["reply_mode"] = "off"
    plugin.set_plugin_config(data)
    result = asyncio.run(plugin.on_before_send(**kwargs))
    assert result is not None and result["modified_kwargs"]["set_reply"] is False
    assert result["modified_kwargs"]["reply_message_id"] == "m1"  # 其余参数原样保留
    qq = {**kwargs, "message": {"platform": "qq"}}
    assert asyncio.run(plugin.on_before_send(**qq)) is None  # 其他平台不动


def test_link_state_shows_in_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        await wait_for(lambda: plugin._supervisor.status.state == "ready")
        await plugin._on_sidecar_event("link", {"state": "reconnecting", "detail": "ENETUNREACH"})
        await plugin.handle_status(stream_id="s1")
        await plugin._on_sidecar_event("link", {"state": "ok"})
        await plugin.handle_status(stream_id="s1")
        await plugin.on_unload()

    asyncio.run(main())
    texts = [p["args"]["text"] for p in host.of("cap.call") if p["capability"] == "send.text"]
    assert "正在自动重连" in texts[0]
    assert "✅ 已连接" in texts[1] and "最近一次中断" in texts[1] and "已自动恢复" in texts[1]
