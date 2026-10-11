from __future__ import annotations

from imessage_adapter_under_test.core.inbound import build_maibot_message


def loader_ok(path: str, mime: str):
    return f"B64({path})", mime


def loader_fail(path: str, mime: str):
    return None, "坏文件"


def make(parts, **extra):
    data = {"message_id": "m1", "chat_id": "any;-;+86138", "sender_id": "+86138", "timestamp": 1700000000000, "parts": parts}
    data.update(extra)
    return data


def test_text_message_shape():
    message = build_maibot_message(make([{"type": "text", "text": "你好"}]), loader_ok)
    assert message["message_id"] == "m1"
    assert message["platform"] == "imessage"
    assert message["session_id"] == "any;-;+86138"
    assert message["message_info"]["user_info"]["user_id"] == "+86138"
    assert message["raw_message"] == [{"type": "text", "data": "你好"}]
    assert message["timestamp"] == "1700000000.0"
    assert message["is_picture"] is False


def test_text_with_two_images_keeps_everything():
    parts = [
        {"type": "text", "text": "看看"},
        {"type": "image", "path": "/tmp/a.jpg", "mime": "image/jpeg"},
        {"type": "image", "path": "/tmp/b.jpg", "mime": "image/jpeg"},
    ]
    message = build_maibot_message(make(parts), loader_ok)
    assert [seg["type"] for seg in message["raw_message"]] == ["text", "image", "image"]
    assert message["raw_message"][1]["binary_data_base64"] == "B64(/tmp/a.jpg)"
    assert message["is_picture"] is True


def test_unreadable_image_becomes_readable_text():
    message = build_maibot_message(make([{"type": "image", "path": "/tmp/x", "mime": "image/heic"}]), loader_fail)
    assert message["raw_message"] == [{"type": "text", "data": "[对方发来一张图片，但没能读取：坏文件]"}]


def test_reply_segment_goes_first():
    message = build_maibot_message(
        make([{"type": "text", "text": "会的"}], reply_to={"message_id": "bot-1", "text": "明天下雨吗"}), loader_ok
    )
    assert message["raw_message"][0] == {
        "type": "reply",
        "data": {"target_message_id": "bot-1", "target_message_content": "明天下雨吗"},
    }
    assert message["raw_message"][1] == {"type": "text", "data": "会的"}
    assert message["reply_to"] == "bot-1"


def test_non_image_parts_become_sentences():
    parts = [
        {"type": "contact", "name": "张三", "phones": ["+86 138"], "emails": ["z@x.com"], "urls": [], "org": "某公司"},
        {"type": "voice", "mime": "audio/x-caf", "duration": 4.4},
        {"type": "file", "mime": "application/pdf", "name": "合同.pdf", "size": 2 * 1024 * 1024},
        {"type": "file", "mime": "image/jpeg", "name": "big.jpg", "size": 80 * 1024 * 1024, "note": "too_large"},
        {"type": "link", "url": "https://example.com"},
        {"type": "poll", "title": "吃什么", "options": ["面", "饭"]},
        {"type": "unsupported", "what": "something-new"},
    ]
    message = build_maibot_message(make(parts), loader_ok)
    assert len(message["raw_message"]) == 1  # 相邻文字合并
    text = message["raw_message"][0]["data"]
    for expected in (
        "[对方发来一张联系人名片] 姓名：张三；电话：+86 138；邮箱：z@x.com；单位：某公司",
        "[对方发来一条语音消息，约 4 秒，你暂时听不了语音]",
        "[对方发来一个文件：合同.pdf（application/pdf，2.0MB），你暂时打不开这类文件]",
        "文件太大，没有接收",
        "[链接] https://example.com",
        "[对方发起了一个投票] 吃什么：面 / 饭",
        "[对方发了一条暂不支持的内容：something-new]",
    ):
        assert expected in text


def test_never_empty():
    message = build_maibot_message(make([]), loader_ok)
    assert message["raw_message"] == [{"type": "text", "data": "[对方发了一条空白消息]"}]
