"""/imessage_missed：解析时间段、整理 Photon 上的消息记录。"""

from __future__ import annotations

from datetime import datetime

from imessage_adapter_under_test.core.history import format_history, parse_range

NOW = datetime(2026, 10, 19, 14, 0, 30)


def test_parse_range_forms() -> None:
    assert parse_range("", NOW)[0] == (datetime(2026, 10, 19, 13, 0, 30), NOW)
    assert parse_range("90", NOW)[0] == (datetime(2026, 10, 19, 12, 30, 30), NOW)
    assert parse_range("13:40", NOW)[0] == (datetime(2026, 10, 19, 13, 40), NOW)
    assert parse_range("13:40 13:55", NOW)[0] == (datetime(2026, 10, 19, 13, 40), datetime(2026, 10, 19, 13, 55))
    assert parse_range("13：40-13：55", NOW)[0] == (datetime(2026, 10, 19, 13, 40), datetime(2026, 10, 19, 13, 55))
    # 还没到的时刻指昨天；结束早于开始就是跨过午夜
    assert parse_range("23:50 00:10", NOW)[0] == (datetime(2026, 10, 18, 23, 50), datetime(2026, 10, 19, 0, 10))
    assert parse_range("0", NOW)[0] is None and "1 到" in parse_range("0", NOW)[1]
    assert parse_range("昨天", NOW)[0] is None and "格式不对" in parse_range("昨天", NOW)[1]
    assert parse_range("25:00", NOW)[0] is None


def test_format_history_marks_missing_messages_without_content() -> None:
    start, end = datetime(2026, 10, 19, 13, 40), datetime(2026, 10, 19, 13, 55)
    base = start.timestamp() * 1000
    result = {
        "sidecar_started_at": base + 30_000,
        "messages": [
            {"time": base + 10_000, "from_me": False, "kind": "text", "chars": 5, "received": False},
            {"time": base + 60_000, "from_me": False, "kind": "text", "chars": 38, "received": True},
            {"time": base + 420_000, "from_me": False, "kind": "text", "chars": 523, "received": False},
            {"time": base + 480_000, "from_me": True, "kind": "attachment", "attachments": 1},
        ],
    }
    text = format_history(result, start, end)
    assert "13:40:10 你 · 文字 5 字 · ❔ 适配器重启之前的" in text
    assert "13:41:00 你 · 文字 38 字 · ✅ 适配器收到了" in text
    assert "13:47:00 你 · 文字 523 字 · ❌ 适配器没收到" in text
    assert "13:48:00 AI · 附件（1 个附件）" in text
    assert "你发的 3 条里，适配器没收到的有 1 条，无法判断的 1 条" in text and "Photon 客服" in text
    empty = format_history({"messages": []}, start, end)
    assert "没有消息记录" in empty and "没到 Photon" in empty
