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
    assert message["raw_message"] == [{"type": "text", "data": "[图片（没能读取：坏文件）]"}]


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
        "[联系人名片] 姓名：张三；电话：+86 138；邮箱：z@x.com；单位：某公司",
        "[语音消息（约 4 秒）]",
        "[文件：合同.pdf（application/pdf，2.0MB）]",
        "太大，没有接收",
        "[链接] https://example.com",
        "[投票] 吃什么：面 / 饭",
        "[暂不支持的内容：something-new]",
    ):
        assert expected in text
    # 标签会作为用户的发言存进 MaiBot 的记忆，不能有旁白或给 AI 的指令
    assert "对方" not in text and "你" not in text and "请" not in text


def test_never_empty():
    message = build_maibot_message(make([]), loader_ok)
    assert message["raw_message"] == [{"type": "text", "data": "[空白消息]"}]


def test_text_file_content_goes_to_ai(tmp_path):
    md = tmp_path / "in-1.md"
    md.write_text("# 购物清单\n- 牛奶\n- 面包\n", encoding="utf-8")
    gbk = tmp_path / "in-2.txt"
    gbk.write_bytes("中文编码测试".encode("gb18030"))
    big = tmp_path / "in-3.txt"
    big.write_text("字" * 7000, encoding="utf-8")
    parts = [
        {"type": "text_file", "path": str(md), "mime": "text/markdown", "name": "清单.md", "size": 30},
        {"type": "text_file", "path": str(gbk), "mime": "text/plain", "name": "a.txt", "size": 12},
        {"type": "text_file", "path": str(big), "mime": "text/plain", "name": "长.txt", "size": 21000},
        {"type": "file", "mime": "text/plain", "name": "超大.log", "size": 900000, "note": "text_too_large"},
    ]
    text = build_maibot_message(make(parts), loader_ok)["raw_message"][0]["data"]
    assert "[文件：清单.md，内容如下]\n# 购物清单\n- 牛奶\n- 面包\n[文件内容结束]" in text
    assert "中文编码测试" in text
    assert "只显示了前 6000 个字" in text
    assert "超大.log" in text and "太大，没有读取内容" in text
    assert not md.exists() and not gbk.exists() and not big.exists()  # 临时文件读完即删


def test_untitled_poll_lists_options_only():
    from imessage_adapter_under_test.core.inbound import describe_part

    assert describe_part({"type": "poll", "title": "", "options": ["海边", "爬山"]}) == "[投票] 海边 / 爬山"
    assert describe_part({"type": "poll", "title": "去哪", "options": ["海边"]}) == "[投票] 去哪：海边"


def test_effect_labels_match_iphone_names():
    from imessage_adapter_under_test.core.inbound import describe_part

    # 名称和 iPhone「带效果发送」界面一致（真机截图：震撼、放大、缩小、隐形墨水）
    assert describe_part({"type": "effect", "effect": "com.apple.MobileSMS.expressivesend.impact"}) == "[震撼特效]"
    assert describe_part({"type": "effect", "effect": "com.apple.MobileSMS.expressivesend.gentle"}) == "[缩小特效]"
    assert describe_part({"type": "effect", "effect": "com.apple.messages.effect.CKShootingStarEffect"}) == "[CKShootingStarEffect特效]"
    assert describe_part({"type": "text_effect", "effect": "shake", "text": "会谢"}) == "[文字效果：正文里「会谢」这几个字带「摇晃」效果]"
    assert describe_part({"type": "text_effect", "effect": "newthing", "text": "x"}) == "[文字效果：正文里「x」这几个字带「newthing」效果]"



def test_sticker_goes_to_maibot_emoji_system():
    sticker = {"message_id": "s1", "chat_id": "c", "sender_id": "+1", "timestamp": 0,
               "parts": [{"type": "image", "path": "/tmp/x.png", "mime": "image/png", "name": "x.png", "size": 3, "sticker": True}]}
    message = build_maibot_message(sticker, loader_ok)
    assert message["raw_message"][0]["type"] == "emoji"
    assert message["is_emoji"] is True

    # 贴在某条消息上的贴纸：引用那条消息 + 贴纸
    placed = dict(sticker, reply_to={"message_id": "BOT-1", "text": "晚安"})
    message = build_maibot_message(placed, loader_ok)
    assert [seg["type"] for seg in message["raw_message"]] == ["reply", "emoji"]

    mixed = dict(sticker, parts=[{"type": "text", "text": "哈"}, *sticker["parts"]])
    assert build_maibot_message(mixed, loader_ok)["is_emoji"] is False
    missing = dict(sticker, parts=[{"type": "sticker"}])
    assert build_maibot_message(missing, loader_ok)["raw_message"] == [{"type": "text", "data": "[贴纸]"}]



def test_text_effect_without_text():
    from imessage_adapter_under_test.core.inbound import describe_part

    assert describe_part({"type": "text_effect", "effect": "explode", "text": ""}) == "[文字效果「爆发」]"
