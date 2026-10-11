"""对方的点赞、投票、已读 → 给 AI 的系统通知；对方 → 聊天流的反向查找。"""

from __future__ import annotations

import asyncio

from imessage_adapter_under_test.core.events import describe_control_event
from imessage_adapter_under_test.core.sessions import SessionDirectory


def test_reaction_notice():
    text = describe_control_event({"type": "reaction", "emoji": "❤️", "target_text": "晚安", "target_from_me": True})
    assert text == "<imessage_event>对方给你的消息「晚安」点了「爱心」回应</imessage_event>"
    # 不带变体选择符的 ❤ 也认得；对方点自己的消息
    own = describe_control_event({"type": "reaction", "emoji": "❤", "target_from_me": False})
    assert own == "<imessage_event>对方给自己发的消息点了「爱心」回应</imessage_event>"
    # iOS 18 起可以用任意表情回应
    custom = describe_control_event({"type": "reaction", "emoji": "🎉", "target_text": "x" * 50})
    assert "「🎉」" in custom and "x" * 30 + "…" in custom


def test_poll_and_read_notice():
    vote = describe_control_event({"type": "poll_vote", "poll_title": "吃什么", "option": "面", "selected": True})
    assert vote == "<imessage_event>对方在投票「吃什么」里选了「面」</imessage_event>"
    undo = describe_control_event({"type": "poll_vote", "poll_title": "吃什么", "option": "面", "selected": False})
    assert "取消了「面」" in undo
    untitled = describe_control_event({"type": "poll_vote", "poll_title": "", "option": "面", "selected": True})
    assert untitled == "<imessage_event>对方在投票里选了「面」</imessage_event>"
    assert describe_control_event({"type": "read"}) == "<imessage_event>对方已读了你发的消息</imessage_event>"
    assert describe_control_event({"type": "other", "content_type": "typing"}) is None


def test_session_lookup_prefers_current_bot_account():
    streams = [
        {"session_id": "old", "user_id": "+1", "account_id": "", "is_group_session": False},
        {"session_id": "new", "user_id": "+1", "account_id": "+19998887777", "is_group_session": False},
        {"session_id": "grp", "user_id": "+1", "account_id": "", "is_group_session": True},
    ]
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"success": True, "streams": streams}

    async def main():
        directory = SessionDirectory(fetch, min_refresh_seconds=0)
        assert await directory.session_for("+1", "+19998887777") == "new"
        assert await directory.session_for("+1", "") == "old"
        assert await directory.session_for("+2", "") is None
        # 账号对不上时取最近的一个
        assert await directory.session_for("+1", "+10000000000") in ("old", "new")

    asyncio.run(main())
    assert calls >= 1
