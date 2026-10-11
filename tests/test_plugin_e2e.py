"""端到端：把整个插件放进一个假的 MaiBot 环境里，用假侧车跑一遍收和发。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncio
import base64
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
            "plugin": {"enabled": True, "config_version": "2.2.0"},
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


async def push_config(plugin: Any, data: dict[str, Any]) -> None:
    """按 MaiBot 的顺序推送配置：先把新配置注入插件，再调用 on_config_update。"""
    plugin.set_plugin_config(copy.deepcopy(data))
    await plugin.on_config_update("self", data, "")


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
    plugin.set_plugin_config({"plugin": {"enabled": False, "config_version": "2.2.0"}, "photon": {"project_id": "p", "project_secret": "s"}})

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


def test_react_can_be_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        never = await plugin.tool_react(msg_id="m1", kind="love", remove=True, stream_id="s-imessage")
        assert never["success"] is False and "没找到你给这条消息点过的「爱心」" in never["error"]
        await plugin.tool_react(msg_id="m1", kind="love", stream_id="s-imessage")
        removed = await plugin.tool_react(msg_id="m1", kind="love", remove=True, stream_id="s-imessage")
        assert removed == {"success": True, "content": "已撤回给这条消息点的「爱心」"}
        await plugin.on_unload()

    asyncio.run(main())
    unsends = [op for op in sidecar_ops(tmp_path) if op["op"] == "unsend"]
    assert len(unsends) == 1 and unsends[0]["args"]["message_id"].startswith("rx-")


def test_edit_and_unsend_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        # 一条文字加图片的回复，会发成两条 iMessage
        sent = await plugin.send_to_imessage(
            {
                "raw_message": [{"type": "text", "data": "看这张"}, {"type": "image", "binary_data_base64": png}],
                "message_info": {"additional_config": {"platform_io_target_user_id": "+1"}},
            }
        )
        first = sent["external_message_id"]

        edited = await plugin.tool_edit(msg_id=first, text="**改好了**", stream_id="s-imessage")
        assert edited == {"success": True, "content": "已把这条消息改成：**改好了**"}
        empty = await plugin.tool_edit(msg_id=first, text="  ", stream_id="s-imessage")
        assert empty["success"] is False
        not_mine = await plugin.tool_edit(msg_id="in-1", text="x", stream_id="s-imessage")
        assert "只能改你自己发的消息" in not_mine["error"]
        late = await plugin.tool_unsend(msg_id="late", stream_id="s-imessage")
        assert late["success"] is False and "unsend window expired" in late["error"]

        unsent = await plugin.tool_unsend(msg_id=first, stream_id="s-imessage")
        assert unsent == {"success": True, "content": "已撤回这条消息"}

        # 关掉开关后工具拒绝执行
        data = plugin.get_plugin_config_data()
        data["capabilities"]["edit_unsend"] = False
        plugin.set_plugin_config(data)
        off = await plugin.tool_unsend(msg_id=first, stream_id="s-imessage")
        assert off["success"] is False and "已在设置里关闭" in off["error"]
        await plugin.on_unload()

    asyncio.run(main())
    ops = sidecar_ops(tmp_path)
    edit = next(op for op in ops if op["op"] == "edit")
    assert "markdown" not in edit["args"]  # iMessage 只能改成纯文字
    unsends = [op["args"]["message_id"] for op in ops if op["op"] == "unsend" and op["args"]["message_id"] != "late"]
    # 两部分都撤回，后发的先撤
    assert unsends[0].endswith("-1") and unsends[1] == unsends[0][:-2]


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
        await push_config(plugin, data)
        assert plugin._supervisor is supervisor and supervisor.status.pid == pid  # 没重启
        assert plugin.config.capabilities.read_receipts is False  # 但新配置已生效

        data = copy.deepcopy(plugin.get_plugin_config_data())
        data["capabilities"]["read_text_files"] = False  # 这一项由侧车决定，需要重启
        await push_config(plugin, data)
        assert plugin._supervisor is not supervisor
        await plugin.on_unload()

    asyncio.run(main())


def test_bot_number_change_is_reported_without_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        supervisor = plugin._supervisor
        before = len(host.of("host.update_message_gateway_state"))

        data = copy.deepcopy(plugin.get_plugin_config_data())
        data["photon"]["bot_number"] = "+1 415 555 0100"
        await push_config(plugin, data)
        assert plugin._supervisor is supervisor  # 没重启
        states = host.of("host.update_message_gateway_state")[before:]
        assert [s["account_id"] for s in states if s["ready"]] == ["+14155550100"]

        # 再推一次同样的配置，不重复上报
        await push_config(plugin, copy.deepcopy(data))
        assert len(host.of("host.update_message_gateway_state")) == before + 1
        await plugin.on_unload()

    asyncio.run(main())


def test_photon_credential_change_restarts_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        supervisor = plugin._supervisor
        data = copy.deepcopy(plugin.get_plugin_config_data())
        data["photon"]["project_secret"] = "NEWSECRET"
        await push_config(plugin, data)
        assert plugin._supervisor is not None and plugin._supervisor is not supervisor
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


def test_effect_and_poll_tools_go_through_maibot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        sent = await plugin.tool_send_effect(text="生日快乐", effect="fireworks", stream_id="s-imessage")
        assert sent["success"] is True and "烟花" in sent["content"]
        bad = await plugin.tool_send_effect(text="x", effect="rainbow", stream_id="s-imessage")
        assert bad["success"] is False

        poll = await plugin.tool_poll(title="周末去哪", options=["海边", "爬山", "海边"], stream_id="s-imessage")
        assert poll["success"] is True and "海边 / 爬山" in poll["content"]
        too_few = await plugin.tool_poll(title="?", options=["只有一个"], stream_id="s-imessage")
        assert too_few["success"] is False

        # MaiBot 把消息交回网关时，标记会变成 dict 段；网关据此加特效
        await plugin.send_to_imessage(
            {
                "raw_message": [
                    {"type": "text", "data": "生日快乐"},
                    {"type": "dict", "data": {"type": "烟花特效", "imessage": {"effect": "fireworks"}}},
                ],
                "message_info": {"additional_config": {"platform_io_target_user_id": "+1"}},
            }
        )
        await plugin.on_unload()

    asyncio.run(main())
    hybrids = [p for p in host.of("cap.call") if p["capability"] == "send.hybrid"]
    assert len(hybrids) == 2
    effect_args = hybrids[0]["args"]
    assert effect_args["stream_id"] == "s-imessage"
    assert effect_args["sync_to_maisaka_history"] is True
    assert effect_args["segments"][1]["data"] == {"type": "烟花特效", "imessage": {"effect": "fireworks"}}
    assert hybrids[1]["args"]["segments"][0]["data"]["imessage"]["poll"] == {"title": "周末去哪", "options": ["海边", "爬山"]}
    send = next(op for op in sidecar_ops(tmp_path) if op["op"] == "send")
    assert send["args"]["parts"] == [{"type": "text", "text": "生日快乐", "effect": "fireworks"}]


def test_background_and_contact_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        assert (await plugin.tool_background(action="set", msg_id="img", stream_id="s-imessage"))["success"] is True
        assert (await plugin.tool_background(action="clear", stream_id="s-imessage"))["content"] == "已清除聊天背景"
        no_image = await plugin.tool_background(action="set", msg_id="txt", stream_id="s-imessage")
        assert "没有图片" in no_image["error"]
        missing_id = await plugin.tool_background(action="set", stream_id="s-imessage")
        assert missing_id["success"] is False
        shared = await plugin.tool_share_contact(stream_id="s-imessage")
        assert shared["success"] is True
        await plugin.on_unload()

    asyncio.run(main())
    ops = [op for op in sidecar_ops(tmp_path) if op["op"] in ("background", "share_contact")]
    assert [op["op"] for op in ops] == ["background", "background", "background", "share_contact"]
    assert ops[0]["args"] == {"chat_id": "+1", "action": "set", "message_id": "img"}
    assert ops[1]["args"] == {"chat_id": "+1", "action": "clear"}


def test_control_events_are_told_to_the_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)

    def control(event: dict) -> dict:
        return {"event_id": "e", "chat_id": "any;-;+1", "sender_id": "+1", "timestamp": 0, "event": event}

    async def drain() -> None:
        while plugin._background:
            await asyncio.sleep(0.01)

    async def main() -> None:
        await plugin.on_load()
        await wait_for(lambda: plugin._supervisor is not None and plugin._supervisor.is_ready)
        await plugin._on_sidecar_event("control", control({"type": "reaction", "emoji": "❤️", "target_text": "晚安", "target_from_me": True}))
        await plugin._on_sidecar_event("control", control({"type": "poll_vote", "poll_title": "吃什么", "option": "面", "selected": True}))
        # 还没发过消息：已读不提醒
        await plugin._on_sidecar_event("control", control({"type": "read"}))
        await drain()
        await plugin.send_to_imessage({"raw_message": [{"type": "text", "data": "在吗"}], "message_info": {"additional_config": {"platform_io_target_user_id": "+1"}}})
        # 发过消息后第一次已读提醒，第二次不再提醒
        await plugin._on_sidecar_event("control", control({"type": "read"}))
        await drain()
        await plugin._on_sidecar_event("control", control({"type": "read"}))
        await drain()

        # 查找聊天流出错时不抛异常，这批已读也不算提醒过
        original = plugin._sessions.session_for

        async def broken(*args: Any) -> None:
            raise RuntimeError("host 不可用")

        plugin._sessions.session_for = broken
        await plugin.send_to_imessage({"raw_message": [{"type": "text", "data": "还在吗"}], "message_info": {"additional_config": {"platform_io_target_user_id": "+1"}}})
        await plugin._on_sidecar_event("control", control({"type": "read"}))
        await drain()
        plugin._sessions.session_for = original
        await plugin._on_sidecar_event("control", control({"type": "read"}))
        await drain()

        # 关掉开关后不再告诉 AI
        data = plugin.get_plugin_config_data()
        data["capabilities"]["notify_reactions"] = False
        plugin.set_plugin_config(data)
        await plugin._on_sidecar_event("control", control({"type": "reaction", "emoji": "👍"}))
        await drain()
        await plugin.on_unload()

    asyncio.run(main())
    appended = [p["args"] for p in host.of("cap.call") if p["capability"] == "maisaka.context.append"]
    texts = [a["visible_text"] for a in appended]
    assert texts == [
        "<imessage_event>对方给你的消息「晚安」点了「爱心」回应</imessage_event>",
        "<imessage_event>对方在投票「吃什么」里选了「面」</imessage_event>",
        "<imessage_event>对方已读了你发的消息</imessage_event>",
        "<imessage_event>对方已读了你发的消息</imessage_event>",  # 上一次没告诉成，重试时补上
    ]
    assert all(a["stream_id"] == "s-imessage" and a["source_kind"] == "imessage_event" for a in appended)
    assert appended[0]["segments"] == [{"type": "text", "data": texts[0]}]


def test_voice_is_transcribed_or_politely_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, host = make_plugin(tmp_path, monkeypatch, emit=False)
    answers = iter([{"success": True, "text": "明天几点见", "content": "明天几点见"}, {"success": False, "error": "未配置 voice 任务"}])

    original_rpc = host.rpc

    async def rpc(method: str, plugin_id: str, payload: dict, timeout_ms: int | None = None) -> Any:
        if method == "cap.call" and payload["capability"] == "llm.transcribe_audio":
            host.calls.append((method, payload))
            return next(answers)
        return await original_rpc(method, plugin_id, payload, timeout_ms)

    plugin._set_context(PluginContext("com.galeros.imessage_adapter", rpc, PluginPaths(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime")))

    def voice_message(message_id: str, path: Path | None) -> dict:
        part: dict = {"type": "voice", "mime": "audio/x-caf", "duration": 3}
        if path is not None:
            path.write_bytes(b"caf-bytes")
            part["path"] = str(path)
        return {"message_id": message_id, "chat_id": "any;-;+1", "sender_id": "+1", "timestamp": 0, "parts": [part]}

    async def main() -> None:
        data = plugin.get_plugin_config_data()
        data["voice"]["transcribe"] = True
        plugin.set_plugin_config(data)
        await plugin._handle_inbound_message(voice_message("v1", tmp_path / "v1.caf"))
        await plugin._handle_inbound_message(voice_message("v2", tmp_path / "v2.caf"))
        # 关闭时侧车不下载语音，没有 path
        data["voice"]["transcribe"] = False
        plugin.set_plugin_config(data)
        await plugin._handle_inbound_message(voice_message("v3", None))

    asyncio.run(main())
    routed = [p["message"]["raw_message"][0]["data"] for p in host.of("host.route_message")]
    assert routed == ["[语音消息（约 3 秒）] 明天几点见", "[语音消息（约 3 秒），未能识别]", "[语音消息（约 3 秒）]"]
    asr = [p["args"] for p in host.of("cap.call") if p["capability"] == "llm.transcribe_audio"]
    assert len(asr) == 2 and asr[0]["audio_base64"] == base64.b64encode(b"caf-bytes").decode()
    assert not list(tmp_path.glob("*.caf"))  # 临时文件用完即删
