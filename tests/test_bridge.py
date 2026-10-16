from __future__ import annotations

from pathlib import Path

import asyncio
import logging
import os
import shutil

import pytest

from imessage_adapter_under_test.core.bridge import SidecarError, SidecarProcess, SidecarSupervisor

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


# ── 以下回归测试来自外部审查（Codex）复现的问题 ─────────────────────────


async def start_process(on_event, mode: str = "ok") -> SidecarProcess:
    process = SidecarProcess(logging.getLogger("test.bridge"), on_event, lambda text: text.replace("TOPSECRET", "***"))
    await process.start(NODE, FAKE, {**os.environ, "FAKE_MODE": mode, "FAKE_EMIT": "0", "PHOTON_PROJECT_SECRET": "TOPSECRET"})
    await asyncio.wait_for(process.ready.wait(), 5)
    return process


async def kill(process: SidecarProcess) -> None:
    if process._process is not None and process._process.returncode is None:
        process._process.kill()
    await process.wait()


def test_malformed_receipt_does_not_stop_reading():
    async def main():
        async def on_event(event, data):
            pass

        process = await start_process(on_event)
        try:
            # 坏回执被跳过，紧接着的正常回执照样收到；之后的请求也正常
            assert await process.request("malformed", timeout=2) == {"after": "malformed"}
            assert (await process.request("ping", timeout=2))["echo"] == "ping"
        finally:
            await kill(process)

    run(main())


def test_error_messages_are_redacted():
    async def main():
        async def on_event(event, data):
            pass

        process = await start_process(on_event)
        try:
            with pytest.raises(SidecarError) as caught:
                await process.request("leak", timeout=2)
            assert "TOPSECRET" not in str(caught.value) and "***" in caught.value.message
        finally:
            await kill(process)

    run(main())


def test_request_timeout_covers_a_blocked_pipe():
    async def main():
        async def on_event(event, data):
            pass

        process = await start_process(on_event, mode="stall-input")
        try:
            started = asyncio.get_running_loop().time()
            with pytest.raises(SidecarError) as caught:
                # 2MB 写进不再读取的管道会卡在 drain；总时限要把这段也算进去
                await process.request("send", {"text": "x" * (2 * 1024 * 1024)}, timeout=0.3)
            assert caught.value.code == "TIMEOUT"
            assert asyncio.get_running_loop().time() - started < 2
        finally:
            await kill(process)

    run(main())


def test_backlog_is_still_processed_after_the_sidecar_exits():
    async def main():
        gate = asyncio.Event()
        seen: list[str] = []

        async def on_event(event, data):
            if event == "message":
                await gate.wait()
                seen.append(data["message_id"])

        process = await start_process(on_event)
        await process.request("burst", {"count": 50}, timeout=2)
        with pytest.raises(SidecarError):
            await process.request("crash", timeout=2)
        await asyncio.wait_for(process.wait(), 5)  # 侧车退出后立刻返回，不等积压处理完
        dispatch = process.dispatch_task
        assert dispatch is not None and not dispatch.done()
        gate.set()
        await asyncio.wait_for(dispatch, 5)
        assert seen == [f"burst-{i}" for i in range(50)]  # 一条不丢，顺序不变

    run(main())


def test_backoff_does_not_overflow_after_days_of_failures():
    h = Harness(mode="crash-on-start")

    async def main():
        # 断网几天后连续失败上千次：以前算 2 的上千次方会溢出，监督循环就此退出
        h.supervisor.status.consecutive_failures = 1025
        await h.start()
        await h.wait_for(lambda: h.supervisor.status.state == "waiting_retry")
        assert h.supervisor._loop_task is not None and not h.supervisor._loop_task.done()
        assert h.supervisor.status.next_retry_at is not None
        await h.supervisor.stop()

    run(main())
