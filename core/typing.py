"""「正在输入」提示的开关时机。

- 开始：MaiBot 的 replyer 开始生成回复时（maisaka.replyer.before_request）。
  这时 AI 已经决定要回复，所以「决定不回复」时对方不会看到输入提示闪一下。
- 结束：这条回复发出后；或者超时（默认 60 秒）还没发出去（例如生成失败），兜底关掉。
"""

from __future__ import annotations

from typing import Awaitable, Callable, Dict

import asyncio
import contextlib
import logging

SendTyping = Callable[[str, str], Awaitable[None]]  # (chat_id, "start"/"stop")


class TypingController:
    def __init__(self, send: SendTyping, logger: logging.Logger, timeout: float = 60.0) -> None:
        self._send = send
        self._logger = logger
        self._timeout = timeout
        self._timers: Dict[str, asyncio.Task[None]] = {}

    @property
    def active_chats(self) -> set[str]:
        return set(self._timers)

    async def _safe_send(self, chat_id: str, state: str) -> None:
        try:
            await self._send(chat_id, state)
        except Exception as exc:
            self._logger.warning("「正在输入」%s 失败：%s", "开启" if state == "start" else "关闭", exc)

    async def _expire(self, chat_id: str) -> None:
        await asyncio.sleep(self._timeout)
        self._timers.pop(chat_id, None)
        await self._safe_send(chat_id, "stop")

    async def start(self, chat_id: str) -> None:
        self._cancel_timer(chat_id)
        self._timers[chat_id] = asyncio.create_task(self._expire(chat_id))
        await self._safe_send(chat_id, "start")

    async def finish(self, chat_id: str) -> None:
        """一条回复发出后调用；没开过输入提示就什么都不做。"""
        if self._cancel_timer(chat_id):
            await self._safe_send(chat_id, "stop")

    def _cancel_timer(self, chat_id: str) -> bool:
        timer = self._timers.pop(chat_id, None)
        if timer is None:
            return False
        timer.cancel()
        return True

    async def close(self) -> None:
        for chat_id in list(self._timers):
            self._cancel_timer(chat_id)
        with contextlib.suppress(Exception):
            await asyncio.sleep(0)
