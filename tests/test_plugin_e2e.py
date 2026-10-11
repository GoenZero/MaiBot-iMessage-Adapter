"""端到端：把整个插件放进一个假的 MaiBot 环境里，用假侧车跑一遍收和发。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncio
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
