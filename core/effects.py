"""iMessage 消息特效与 iOS 18 文字效果的名称表。

收（告诉 AI 对方用了什么特效）和发（imessage_send_effect 工具）共用一张表，
中文名尽量和 iPhone「带效果发送」界面上的叫法一致，这样 AI 说的和使用者看到的对得上。
"""

from __future__ import annotations

from typing import Dict, Tuple

# 工具里用的短名 → (Apple 的特效编号, 中文名)
MESSAGE_EFFECTS: Dict[str, Tuple[str, str]] = {
    # 气泡特效（iPhone 上「气泡」那一页，名称已和真机截图核对）
    "slam": ("com.apple.MobileSMS.expressivesend.impact", "震撼"),
    "loud": ("com.apple.MobileSMS.expressivesend.loud", "放大"),
    "gentle": ("com.apple.MobileSMS.expressivesend.gentle", "缩小"),
    "invisible": ("com.apple.MobileSMS.expressivesend.invisibleink", "隐形墨水"),
    # 屏幕特效（iPhone 上「屏幕」那一页）
    "echo": ("com.apple.messages.effect.CKEchoEffect", "回声"),
    "spotlight": ("com.apple.messages.effect.CKSpotlightEffect", "聚光灯"),
    "balloons": ("com.apple.messages.effect.CKBalloonEffect", "气球"),
    "confetti": ("com.apple.messages.effect.CKConfettiEffect", "五彩纸屑"),
    "heart": ("com.apple.messages.effect.CKHeartEffect", "爱心"),
    "lasers": ("com.apple.messages.effect.CKLasersEffect", "激光"),
    "fireworks": ("com.apple.messages.effect.CKFireworksEffect", "烟花"),
    "celebration": ("com.apple.messages.effect.CKHappyBirthdayEffect", "庆祝"),
    "sparkles": ("com.apple.messages.effect.CKSparklesEffect", "闪光"),
}

EFFECT_NAMES: Dict[str, str] = {short: name for short, (_, name) in MESSAGE_EFFECTS.items()}
NAME_BY_APPLE_ID: Dict[str, str] = {apple_id: name for apple_id, name in MESSAGE_EFFECTS.values()}

# iOS 18 文字效果（选中几个字后套的动效），Photon 送来的 effectName → 中文名
TEXT_EFFECT_NAMES: Dict[str, str] = {
    "big": "放大",
    "small": "缩小",
    "shake": "摇晃",
    "nod": "点头",
    "explode": "爆发",  # 和 iPhone 上的叫法一致（真机截图核对）
    "ripple": "波纹",
    "bloom": "绽放",
    "jitter": "抖动",
}


def effect_name(apple_id: str) -> str:
    """Apple 特效编号 → 中文名；不认识的编号取最后一段，例如 CKShootingStarEffect。"""
    return NAME_BY_APPLE_ID.get(apple_id) or apple_id.rsplit(".", 1)[-1]


def text_effect_name(effect: str) -> str:
    return TEXT_EFFECT_NAMES.get(effect.lower(), effect)
