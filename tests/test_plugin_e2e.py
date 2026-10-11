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

    async def fake_resolve(configured: str, logger: Any) -> tuple[str, str]:
        return NODE, "vtest"

    monkeypatch.setattr(plugin_module, "resolve_node", fake_resolve)
    return plugin, host


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
