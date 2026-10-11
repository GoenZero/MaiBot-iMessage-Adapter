from __future__ import annotations

import base64

from imessage_adapter_under_test.core.outbound import build_outbound_plan, resolve_target, sniff_image_mime

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20


def test_text_and_image(tmp_path):
    message = {
        "raw_message": [
            {"type": "text", "data": "给你"},
            {"type": "text", "data": "看"},
            {"type": "image", "data": "", "hash": "h", "binary_data_base64": base64.b64encode(JPEG).decode()},
        ]
    }
    plan = build_outbound_plan(message, tmp_path, 1024)
    assert plan.parts[0] == {"type": "text", "text": "给你看"}
    assert plan.parts[1]["type"] == "file" and plan.parts[1]["mime"] == "image/jpeg"
    assert (tmp_path / plan.parts[1]["path"].split("/")[-1]).read_bytes() == JPEG
    plan.cleanup()
    assert list(tmp_path.iterdir()) == []


def test_reply_target_and_skips(tmp_path):
    message = {
        "raw_message": [
            {"type": "reply", "data": {"target_message_id": "in-1", "target_message_content": None}},
            {"type": "text", "data": "好的"},
            {"type": "voice", "data": "", "binary_data_base64": "AAAA"},
            {"type": "dict", "data": {"type": "imessage_effect", "data": {}}},
        ]
    }
    plan = build_outbound_plan(message, tmp_path, 1024)
    assert plan.reply_to == "in-1"
    assert plan.parts == [{"type": "text", "text": "好的"}]
    assert plan.skipped == ["voice", "dict"]


def test_oversize_image_is_skipped(tmp_path):
    big = base64.b64encode(PNG + b"\x00" * 4096).decode()
    plan = build_outbound_plan({"raw_message": [{"type": "image", "binary_data_base64": big}]}, tmp_path, 1024)
    assert plan.parts == []
    assert "超过附件上限" in plan.skipped[0]


def test_plain_text_fallback(tmp_path):
    plan = build_outbound_plan({"raw_message": [], "processed_plain_text": " 旧格式 "}, tmp_path, 1024)
    assert plan.parts == [{"type": "text", "text": "旧格式"}]


def test_sniff():
    assert sniff_image_mime(PNG) == "image/png"
    assert sniff_image_mime(JPEG) == "image/jpeg"
    assert sniff_image_mime(b"GIF89a....") == "image/gif"
    assert sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_image_mime(b"\x00\x00\x00\x18ftypheic") == "image/heic"


def test_resolve_target_uses_platform_io_user_not_bot():
    message = {
        "message_info": {
            "user_info": {"user_id": "+1BOT"},
            "additional_config": {"platform_io_target_user_id": "+86138"},
        }
    }
    assert resolve_target(message) == "+86138"
    assert resolve_target({"message_info": {"user_info": {"user_id": "+1BOT"}, "additional_config": {}}}) == ""


def test_markdown_detection():
    from imessage_adapter_under_test.core.outbound import looks_like_markdown

    for text in ("这是**重点**", "用 `pip install` 装", "# 标题\n内容", "- 苹果\n- 香蕉", "1. 起床\n2. 刷牙", "[官网](https://example.com)", "~~划掉~~"):
        assert looks_like_markdown(text), text
    for text in ("你好呀", "5*3=15", "- 就一行", "今天 #心情 不错", "哈哈*", "1. 只有一行", "a_b_c"):
        assert not looks_like_markdown(text), text
