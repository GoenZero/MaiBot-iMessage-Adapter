"""`/imessage_missed`：对照 Photon 上的消息记录，查对方发的消息适配器有没有收到。

只显示时间、谁发的、字数，不显示消息内容。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import re

DEFAULT_MINUTES = 60
MAX_MINUTES = 24 * 60
MAX_LINES = 40

_KIND_TEXT = {"text": "文字", "attachment": "附件", "reaction": "点按回应", "other": "其他"}


def parse_range(args: str, now: datetime) -> Tuple[Optional[Tuple[datetime, datetime]], str]:
    """解析命令参数，返回 ((开始, 结束), "") 或 (None, 错误说明)。

    支持：不填（最近 60 分钟）、分钟数（如 90）、开始时刻（如 13:40，到现在）、
    时间段（如 13:40 14:00 或 13:40-14:00）。时刻按 MaiBot 所在机器的本地时间，指今天；
    开始时刻还没到时指昨天。
    """
    text = (args or "").strip()
    if not text:
        return (now - timedelta(minutes=DEFAULT_MINUTES), now), ""
    if text.isdigit():
        minutes = int(text)
        if not 1 <= minutes <= MAX_MINUTES:
            return None, f"分钟数要在 1 到 {MAX_MINUTES} 之间"
        return (now - timedelta(minutes=minutes), now), ""
    times = re.findall(r"(\d{1,2})[:：](\d{2})", text)
    if not times or len(times) > 2:
        return None, "格式不对。例：/imessage_missed、/imessage_missed 90、/imessage_missed 13:40 14:00"
    points: List[datetime] = []
    for hour, minute in times:
        if int(hour) > 23 or int(minute) > 59:
            return None, f"时间不对：{hour}:{minute}"
        points.append(now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0))
    start = points[0]
    if start > now:
        start -= timedelta(days=1)
    end = points[1] if len(points) == 2 else now
    while end <= start:
        end += timedelta(days=1)
    if end - start > timedelta(minutes=MAX_MINUTES):
        return None, "时间段最长 24 小时"
    return (start, end), ""


def format_history(result: Dict[str, Any], start: datetime, end: datetime) -> str:
    """把侧车返回的消息记录整理成给人看的文字。"""
    messages = [m for m in result.get("messages") or [] if isinstance(m, dict)]
    started_ms = float(result.get("sidecar_started_at") or 0)
    span = f"{start:%m-%d %H:%M} – {end:%H:%M}"
    if not messages:
        return f"📋 Photon 上 {span} 这个聊天没有消息记录。\n如果你确定这段时间发过消息，说明消息没到 Photon（问题不在适配器）。"

    lines = [f"📋 Photon 上的消息记录（{span}，只显示时间和字数）"]
    missed = unknown = theirs = 0
    shown: List[str] = []
    for item in messages:
        when = datetime.fromtimestamp(float(item.get("time") or 0) / 1000)
        kind = _KIND_TEXT.get(str(item.get("kind")), "其他")
        size = f"{kind} {item.get('chars', 0)} 字" if item.get("kind") == "text" else kind
        if item.get("attachments"):
            size += f"（{item.get('attachments')} 个附件）"
        if item.get("from_me"):
            shown.append(f"{when:%H:%M:%S} AI · {size}")
            continue
        theirs += 1
        if item.get("received"):
            mark = "✅ 适配器收到了"
        elif started_ms and float(item.get("time") or 0) < started_ms:
            mark = "❔ 适配器重启之前的，无法判断"
            unknown += 1
        else:
            mark = "❌ 适配器没收到"
            missed += 1
        shown.append(f"{when:%H:%M:%S} 你 · {size} · {mark}")
    if len(shown) > MAX_LINES:
        lines.append(f"（共 {len(shown)} 条，只显示最后 {MAX_LINES} 条）")
        shown = shown[-MAX_LINES:]
    lines.extend(shown)
    if result.get("truncated"):
        lines.append("（消息太多，只查了前 500 条，可以把时间段缩短一些）")
    summary = f"你发的 {theirs} 条里，适配器没收到的有 {missed} 条"
    if unknown:
        summary += f"，无法判断的 {unknown} 条"
    lines.append(summary + "。")
    if missed:
        lines.append("没收到的消息 Photon 上有，说明是 Photon 的实时消息推送漏了，可以把时间告诉 Photon 客服。")
    if started_ms:
        lines.append(f"适配器（侧车）这次从 {datetime.fromtimestamp(started_ms / 1000):%m-%d %H:%M} 开始运行。")
    return "\n".join(lines)
