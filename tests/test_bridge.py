from __future__ import annotations

from pathlib import Path

import asyncio
import logging
import os
import shutil

import pytest

from imessage_adapter_under_test.core.bridge import SidecarError, SidecarSupervisor

FAKE = Path(__file__).with_name("fake_sidecar.mjs")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="需要 node")


class Harness:
    def __init__(self, mode: str = "ok", emit: bool = False) -> None:
        self.events: list[tuple[str, dict]] = []
        self.ready_changes: list[bool] = []
        self.logger = logging.getLogger("test.bridge")
        self.mode = mode
        self.emit = emit
        self.launches = 0
        self.supervisor = SidecarSupervisor(
            logger=self.logger,
            on_event=self.on_event,
            on_ready_change=self.on_ready,
            redact=lambda text: text.replace("TOPSECRET", "***"),
        )

    async def on_event(self, event, data):
        self.events.append((event, data))

    async def on_ready(self, ready):
        self.ready_changes.append(ready)

    async def launch(self):
        self.launches += 1
        env = {**os.environ, "FAKE_MODE": self.mode, "FAKE_EMIT": "1" if self.emit else "0", "PHOTON_PROJECT_SECRET": "TOPSECRET"}
        return NODE, FAKE, env

    async def start(self, retry_interval: float = 1.0):
        self.supervisor.start(self.launch, fast_retries=2, retry_interval=retry_interval)

    async def wait_for(self, predicate, timeout: float = 10.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("等待超时")
            await asyncio.sleep(0.05)


def run(coro):
    return asyncio.run(coro)


def test_ready_request_and_receipt(caplog):
    async def main():
        h = Harness(emit=True)
        with caplog.at_level(logging.DEBUG, logger="test.bridge"):
            await h.start()
            await h.wait_for(lambda: h.supervisor.is_ready)
            result = await h.supervisor.request("send", {"chat_id": "+1", "parts": [], "reply_to": "x"})
            assert result["message_ids"][0].startswith("out-")
            assert result["quoted"] is True
            with pytest.raises(SidecarError) as info:
                await h.supervisor.request("fail")
            assert info.value.code == "TARGET_NOT_ALLOWED"
            with pytest.raises(SidecarError) as info:
                await h.supervisor.request("slow", timeout=0.3)
            assert info.value.code == "TIMEOUT"
            await h.wait_for(lambda: len(h.events) >= 4)
            # 事件按到达顺序处理
            assert [d.get("message_id") for e, d in h.events if e == "message"] == ["m1", "m2", "m3"]
            await h.supervisor.stop()
        assert h.ready_changes[0] is True and h.ready_changes[-1] is False
        logs = caplog.text
        assert "假侧车启动 secret=***" in logs and "TOPSECRET" not in logs
        assert "这是一行不是协议的输出" in logs

    run(main())


def test_crash_restarts_and_pending_requests_fail():
    async def main():
        h = Harness()
        await h.start(retry_interval=1.0)
        await h.wait_for(lambda: h.supervisor.is_ready)
        with pytest.raises(SidecarError) as info:
            await h.supervisor.request("crash")
        assert info.value.code == "SIDECAR_EXITED"
        await h.wait_for(lambda: h.launches >= 2 and h.supervisor.is_ready)
        assert h.supervisor.status.restarts >= 1
        await h.supervisor.stop()

    run(main())


def test_auth_failure_stops_retrying():
    async def main():
        h = Harness(mode="auth")
        await h.start()
        await h.wait_for(lambda: h.supervisor.status.state == "auth_failed")
        await asyncio.sleep(1.5)
        assert h.launches == 1
        assert "AUTH" in h.supervisor.status.last_error
        await h.supervisor.stop()

    run(main())


def test_manual_restart_restarts_once():
    async def main():
        h = Harness()
        await h.start()
        await h.wait_for(lambda: h.supervisor.is_ready)
        await h.supervisor.restart()
        await h.wait_for(lambda: h.launches == 2 and h.supervisor.is_ready)
        await asyncio.sleep(1.0)
        assert h.launches == 2
        await h.supervisor.stop()

    run(main())


def test_request_when_not_ready():
    async def main():
        h = Harness()
        with pytest.raises(SidecarError) as info:
            await h.supervisor.request("send")
        assert info.value.code == "NOT_READY"

    run(main())
