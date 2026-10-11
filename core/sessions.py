"""MaiBot 聊天流 ID ↔ iMessage 对方的对应关系。

MaiBot 内部用一串哈希（session_id / stream_id）表示一个聊天，插件的 Hook 和工具只拿得到这个 ID。
这里通过 ``chat.get_all_streams(platform="imessage")`` 查出哪些聊天是 iMessage 的、对方是谁，并缓存。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

import time

FetchStreams = Callable[[], Awaitable[Any]]


class SessionDirectory:
    def __init__(self, fetch: FetchStreams, min_refresh_seconds: float = 30.0) -> None:
        self._fetch = fetch
        self._min_refresh = min_refresh_seconds
        self._user_by_session: Dict[str, str] = {}
        self._last_refresh = 0.0

    @staticmethod
    def _extract(result: Any) -> List[Dict[str, Any]]:
        if isinstance(result, dict):
            result = result.get("streams", [])
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    async def refresh(self) -> None:
        self._last_refresh = time.monotonic()
        streams = self._extract(await self._fetch())
        for stream in streams:
            session_id = str(stream.get("session_id") or stream.get("stream_id") or "")
            user_id = str(stream.get("user_id") or "")
            if session_id and user_id and not stream.get("is_group_session"):
                self._user_by_session[session_id] = user_id

    async def user_for(self, session_id: str) -> Optional[str]:
        """返回这个聊天里对方的 iMessage 地址；不是 iMessage 私聊则返回 None。"""
        if not session_id:
            return None
        if session_id not in self._user_by_session and time.monotonic() - self._last_refresh >= self._min_refresh:
            await self.refresh()
        return self._user_by_session.get(session_id)

    def remember(self, session_id: str, user_id: str) -> None:
        if session_id and user_id:
            self._user_by_session[session_id] = user_id
