from __future__ import annotations

import asyncio
import logging

from imessage_adapter_under_test.core.typing import TypingController


def test_start_finish_and_timeout():
    async def main():
        calls = []

        async def send(chat_id, state):
            calls.append((chat_id, state))

        typing = TypingController(send, logging.getLogger("t"), timeout=0.2)
        await typing.start("c1")
        await typing.finish("c1")
        await typing.finish("c1")  # 重复调用不再发 stop
        assert calls == [("c1", "start"), ("c1", "stop")]

        calls.clear()
        await typing.start("c2")  # 生成失败、一直没发出去
        await asyncio.sleep(0.4)
        assert calls == [("c2", "start"), ("c2", "stop")]
        assert typing.active_chats == set()

        calls.clear()
        await typing.finish("c3")  # 没开过就不关
        assert calls == []

    asyncio.run(main())


def test_send_failure_is_only_logged(caplog):
    async def main():
        async def send(chat_id, state):
            raise RuntimeError("boom")

        typing = TypingController(send, logging.getLogger("t"), timeout=5)
        await typing.start("c1")
        await typing.finish("c1")
        await typing.close()

    asyncio.run(main())
    assert "「正在输入」开启 失败" in caplog.text or "失败" in caplog.text
