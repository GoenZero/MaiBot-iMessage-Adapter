"""给 AI 用的 iMessage 工具。

写成混入类，由 plugin.py 的插件类继承（SDK 会扫描插件实例上所有带 @Tool 的方法）。
每个工具都先查能力注册表：能力关闭时，工具已经从 AI 的工具列表里拿掉了，这里再挡一次。

失败时一律返回 success=False 和一句 AI 能看懂的原因（例如「找不到这条消息，这次没点成」），
不抛异常，不影响聊天。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from maibot_sdk import Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

import asyncio
import base64
import binascii
import json
import re
import secrets

from .core.bridge import SidecarError
from .core.capabilities import capability
from .core.effects import EFFECT_NAMES
from .core.lru import LruDict
from .core.media import prepare_sticker

if TYPE_CHECKING:
    from .config import IMessageAdapterConfig
    from .core.bridge import SidecarSupervisor

ACTION_TIMEOUT = 20.0
BACKGROUND_TIMEOUT = 60.0

TAPBACK_NAMES = {"love": "爱心", "like": "赞", "dislike": "踩", "laugh": "哈哈", "emphasize": "感叹号", "question": "问号"}

REACT_DESCRIPTION = (
    "给对方的某条 iMessage 消息加一个 Tapback 回应，就像在 iPhone 上长按消息选爱心或点赞。"
    "适合：对方道谢、分享好消息、说了好笑的话，或你想表示认同但不必专门回一句时。"
    "不要每条消息都点，一轮对话最多点一次；点了回应也可以照常回复。"
    "kind 可选：love=爱心，like=赞，dislike=踩，laugh=哈哈，emphasize=感叹号（强调），question=问号。"
    "remove=true 表示撤回你之前给这条消息点的同一种回应（点错了，或对方让你撤回时用）。"
)
EDIT_DESCRIPTION = (
    "修改你自己发出的一条 iMessage 消息的文字。对方会看到那条消息变成新内容，旁边标着「已编辑」。"
    "只在你刚才说错了、打错字、需要更正时用；不要用它代替正常回复，也不要改对方的消息。"
    "只能改你自己 15 分钟内发的文字消息，每条最多改 5 次；改后按纯文字显示，不支持 Markdown。"
)
UNSEND_DESCRIPTION = (
    "撤回你自己发出的一条 iMessage 消息，对方那边这条消息会消失（显示「撤回了一条消息」）。"
    "只在发错了对象、发错了内容，或对方要你撤回时用；平时不要用。"
    "只能撤回你自己 2 分钟内发的消息。要撤回点赞，请用 imessage_react 并填 remove=true。"
)

EFFECT_DESCRIPTION = (
    "发一条带 iMessage 特效的消息，就像在 iPhone 上长按发送键选特效。"
    "只在特别的时刻用：生日、节日祝福、祝贺好消息、表白、强调一句很重要的话。平时聊天不要用，一天最多一两次。"
    "这条消息就是你这次的回复，发完不要再用 reply 重复同样的话。"
    "effect 可选，气泡特效：slam=震撼，loud=放大，gentle=缩小，invisible=隐形墨水（对方要用手指擦开才能看到）；"
    "屏幕特效：echo=回声，spotlight=聚光灯，balloons=气球，confetti=五彩纸屑，heart=爱心，lasers=激光，"
    "fireworks=烟花，celebration=庆祝，sparkles=闪光。"
)
MAX_POLL_OPTIONS = 12
POLL_DESCRIPTION = (
    "在聊天里发起一个 iMessage 投票，对方可以直接点选项。"
    "适合需要对方在几个选项里做选择时，例如去哪吃饭、周末做什么、几个方案选哪个。"
    "title 是问题，options 是 2 到 12 个简短选项。对方投票后你会收到通知。"
    "这条投票就是你这次的回复，不要再用 reply 重复同样的问题。"
)
BACKGROUND_DESCRIPTION = (
    "设置或清除这个 iMessage 对话的聊天背景，双方都会看到。只在对方明确要求换背景或清除背景时用。"
    "action=set 时用 msg_id 指定一条带图片的消息（通常是对方刚发来的那张图）；action=clear 清除背景，不用填 msg_id。"
)
STICKER_DESCRIPTION = (
    "从你的表情包库里挑一张符合情绪的表情包，当作贴纸贴在对方的某条消息气泡上（就像 iPhone 上长按消息贴贴纸），"
    "不会单独发一条消息。适合想对某句话做个俏皮、可爱的回应时；不要频繁用，一轮对话最多一次。"
    "emotion 写想表达的情绪，例如 开心、无语、害羞、委屈。"
)
SHARE_CONTACT_DESCRIPTION = (
    "用 iMessage 的「共享名字与照片」把你这个号码的名字和头像发给对方。"
    "对方不会收到一条消息，而是在聊天顶部你的名字下面出现「更新」，点了才会改。"
    "名字和头像来自 Photon 账号的设置，不是你的昵称。"
    "只在对方想要你的联系方式、问你是谁、或要你的名片时用；不要主动反复发。"
)


def as_bool(value: Any) -> bool:
    """模型有时把布尔值写成字符串 "false"，不能直接当真值判断。"""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def as_string_list(value: Any) -> List[str]:
    """数组参数：正常是列表；模型偶尔传 JSON 字符串或用顿号、逗号分隔的一串。"""
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = re.split(r"[、,，/\n]+", text)
        value = parsed if isinstance(parsed, list) else [parsed]
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    # 去掉重复项，保留顺序
    return list(dict.fromkeys(items))


class IMessageToolsMixin:
    """AI 工具。依赖插件类提供的 config、ctx、_supervisor、_chat_for_stream。"""

    if TYPE_CHECKING:
        config: IMessageAdapterConfig
        ctx: Any
        _supervisor: Optional[SidecarSupervisor]
        _media_dir: Optional[Path]

        async def _chat_for_stream(self, stream_id: str) -> Optional[str]: ...

    def _init_tool_state(self, cache_size: int) -> None:
        # 一条 MaiBot 回复可能发成好几条 iMessage（文字加图片）：第一条的编号 → 全部编号，撤回时一起撤
        self._sent_groups: LruDict[List[str]] = LruDict(cache_size)
        # AI 点过的回应：「消息编号|类型」→ 回应本身的编号，撤回点赞时要用
        self._my_reactions: LruDict[str] = LruDict(cache_size)

    def _remember_sent(self, message_ids: List[str]) -> None:
        if message_ids:
            self._sent_groups.set(message_ids[0], list(message_ids))

    async def _tool_chat(self, stream_id: str, key: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """检查能力开关和当前聊天；可以执行时返回 (对话编号, None)，否则返回 (None, 给 AI 的失败结果)。"""
        cap = capability(key)
        if not cap.enabled(self.config):
            return None, {"success": False, "error": f"「{cap.title}」已在设置里关闭"}
        chat_id = await self._chat_for_stream(stream_id)
        if self._supervisor is None or chat_id is None:
            return None, {"success": False, "error": f"当前不是 iMessage 聊天，不能使用「{cap.title}」"}
        return chat_id, None

    async def _sidecar_call(self, op: str, args: Dict[str, Any], timeout: float = ACTION_TIMEOUT) -> Dict[str, Any]:
        assert self._supervisor is not None
        return await self._supervisor.request(op, args, timeout=timeout)

    def _tool_failure(self, verb: str, exc: SidecarError) -> Dict[str, Any]:
        """把侧车错误换成 AI 能看懂的一句话。verb 是动作名，例如「点」「改」「撤回」。"""
        self.ctx.logger.warning("iMessage 工具：%s失败 %s", verb, exc)
        if exc.code == "MESSAGE_NOT_FOUND":
            return {"success": False, "error": f"找不到这条消息（可能太久远，或适配器重启过），这次没{verb}成"}
        if exc.code == "NOT_OWN_MESSAGE":
            return {"success": False, "error": f"只能{verb}你自己发的消息，这次没{verb}成"}
        return {"success": False, "error": f"这次没{verb}成：{exc.message}"}

    # ── 点赞回应 ──────────────────────────────────────

    @Tool(
        "imessage_react",
        description=REACT_DESCRIPTION,
        parameters=[
            ToolParameterInfo(name="msg_id", param_type=ToolParamType.STRING, description="要回应的那条消息的 msg_id"),
            ToolParameterInfo(
                name="kind",
                param_type=ToolParamType.STRING,
                description="回应类型",
                enum_values=list(TAPBACK_NAMES),
            ),
            ToolParameterInfo(
                name="remove",
                param_type=ToolParamType.BOOLEAN,
                description="true=撤回你之前点的这个回应；平时不填",
                required=False,
            ),
        ],
        chat_scope="private",
    )
    async def tool_react(
        self, msg_id: str = "", kind: str = "", remove: bool = False, stream_id: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        del kwargs
        chat_id, refused = await self._tool_chat(stream_id, "reactions")
        if refused:
            return refused
        if kind not in TAPBACK_NAMES:
            return {"success": False, "error": f"kind 只能是 {', '.join(TAPBACK_NAMES)}"}
        key = f"{msg_id}|{kind}"
        if as_bool(remove):
            reaction_id = self._my_reactions.get(key)
            if reaction_id is None:
                return {"success": False, "error": f"没找到你给这条消息点过的「{TAPBACK_NAMES[kind]}」（可能适配器重启过），这次没撤回成"}
            try:
                await self._sidecar_call("unsend", {"chat_id": chat_id, "message_id": reaction_id})
            except SidecarError as exc:
                return self._tool_failure("撤回", exc)
            self._my_reactions.pop(key)
            return {"success": True, "content": f"已撤回给这条消息点的「{TAPBACK_NAMES[kind]}」"}
        try:
            result = await self._sidecar_call("react", {"chat_id": chat_id, "message_id": msg_id, "kind": kind})
        except SidecarError as exc:
            return self._tool_failure("点", exc)
        if result.get("reaction_id"):
            self._my_reactions.set(key, str(result["reaction_id"]))
        return {"success": True, "content": f"已给这条消息点了「{TAPBACK_NAMES[kind]}」"}

    # ── 编辑与撤回 ────────────────────────────────────

    @Tool(
        "imessage_edit",
        description=EDIT_DESCRIPTION,
        parameters=[
            ToolParameterInfo(name="msg_id", param_type=ToolParamType.STRING, description="要修改的那条消息（你自己发的）的 msg_id"),
            ToolParameterInfo(name="text", param_type=ToolParamType.STRING, description="改成的完整新内容"),
        ],
        chat_scope="private",
    )
    async def tool_edit(self, msg_id: str = "", text: str = "", stream_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        chat_id, refused = await self._tool_chat(stream_id, "edit_unsend")
        if refused:
            return refused
        if not text.strip():
            return {"success": False, "error": "新内容不能为空；想删掉整条消息请用 imessage_unsend"}
        args: Dict[str, Any] = {"chat_id": chat_id, "message_id": msg_id, "text": text}
        try:
            await self._sidecar_call("edit", args)
        except SidecarError as exc:
            return self._tool_failure("改", exc)
        return {"success": True, "content": f"已把这条消息改成：{text}"}

    @Tool(
        "imessage_unsend",
        description=UNSEND_DESCRIPTION,
        parameters=[
            ToolParameterInfo(name="msg_id", param_type=ToolParamType.STRING, description="要撤回的那条消息（你自己发的）的 msg_id"),
        ],
        chat_scope="private",
    )
    async def tool_unsend(self, msg_id: str = "", stream_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        chat_id, refused = await self._tool_chat(stream_id, "edit_unsend")
        if refused:
            return refused
        # 文字加图片的回复会发成好几条，一起撤回；后发的先撤
        message_ids = self._sent_groups.get(msg_id) or [msg_id]
        done = 0
        for message_id in reversed(message_ids):
            try:
                await self._sidecar_call("unsend", {"chat_id": chat_id, "message_id": message_id})
            except SidecarError as exc:
                if done == 0:
                    return self._tool_failure("撤回", exc)
                self.ctx.logger.warning("撤回多条中的一条失败 %s", exc)
                continue
            done += 1
        self._sent_groups.pop(msg_id)
        if done < len(message_ids):
            return {"success": True, "content": f"这条回复有 {len(message_ids)} 部分，撤回了 {done} 部分"}
        return {"success": True, "content": "已撤回这条消息"}

    # ── 特效与投票：走 MaiBot 的发送流程，聊天记录里能看到 ────────

    async def _send_marked(
        self, stream_id: str, segments: List[Dict[str, Any]], plain_text: str
    ) -> Optional[str]:
        """通过 MaiBot 发出带 iMessage 动作标记的消息，并同步进 AI 的聊天记录。成功返回 None，失败返回原因。"""
        try:
            result = await self.ctx.send.hybrid(
                segments,
                stream_id,
                processed_plain_text=plain_text,
                sync_to_maisaka_history=True,
                # 和 MaiBot 自带的发表情工具一样，标成机器人自己的回复
                maisaka_source_kind="guided_reply",
            )
        except Exception as exc:  # noqa: BLE001 - 任何失败都只告诉 AI，不影响聊天
            self.ctx.logger.warning("iMessage 工具：发送失败 %s", exc)
            return str(exc)
        # SDK 把发送结果还原成 True / False；失败的具体原因 MaiBot 会写进它自己的日志
        return None if result is True else "发送失败（详见 MaiBot 日志）"

    @Tool(
        "imessage_send_effect",
        description=EFFECT_DESCRIPTION,
        parameters=[
            ToolParameterInfo(name="text", param_type=ToolParamType.STRING, description="要发的话"),
            ToolParameterInfo(
                name="effect",
                param_type=ToolParamType.STRING,
                description="特效名称",
                enum_values=list(EFFECT_NAMES),
            ),
        ],
        chat_scope="private",
    )
    async def tool_send_effect(self, text: str = "", effect: str = "", stream_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        _, refused = await self._tool_chat(stream_id, "effects")
        if refused:
            return refused
        if effect not in EFFECT_NAMES:
            return {"success": False, "error": f"effect 只能是 {', '.join(EFFECT_NAMES)}"}
        if not text.strip():
            return {"success": False, "error": "要发的话不能为空"}
        label = f"{EFFECT_NAMES[effect]}特效"
        segments = [
            {"type": "text", "data": text},
            {"type": "imessage", "data": {"type": label, "imessage": {"effect": effect}}},
        ]
        error = await self._send_marked(stream_id, segments, f"{text}（{label}）")
        if error:
            return {"success": False, "error": f"这次没发出去：{error}"}
        return {"success": True, "content": f"已发出带「{EFFECT_NAMES[effect]}」特效的消息：{text}。这就是你这次的回复。"}

    @Tool(
        "imessage_poll",
        description=POLL_DESCRIPTION,
        parameters=[
            ToolParameterInfo(name="title", param_type=ToolParamType.STRING, description="投票的问题"),
            ToolParameterInfo(
                name="options",
                param_type=ToolParamType.ARRAY,
                description="2 到 12 个选项",
                items_schema={"type": "string"},
            ),
        ],
        chat_scope="private",
    )
    async def tool_poll(self, title: str = "", options: Any = None, stream_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        _, refused = await self._tool_chat(stream_id, "polls")
        if refused:
            return refused
        choices = as_string_list(options)
        if not title.strip():
            return {"success": False, "error": "投票要有问题（title）"}
        if not 2 <= len(choices) <= MAX_POLL_OPTIONS:
            return {"success": False, "error": f"投票要有 2 到 {MAX_POLL_OPTIONS} 个不同的选项"}
        label = f"投票「{title.strip()}」（选项：{' / '.join(choices)}）"
        segments = [{"type": "imessage", "data": {"type": label, "imessage": {"poll": {"title": title.strip(), "options": choices}}}}]
        error = await self._send_marked(stream_id, segments, label)
        if error:
            return {"success": False, "error": f"这次没发出去：{error}"}
        return {"success": True, "content": f"已发起{label}。对方投票后你会收到通知。"}

    # ── 聊天背景与名片：不产生消息，直接让侧车执行 ────────

    @Tool(
        "imessage_background",
        description=BACKGROUND_DESCRIPTION,
        parameters=[
            ToolParameterInfo(
                name="action",
                param_type=ToolParamType.STRING,
                description="set=设置背景，clear=清除背景",
                enum_values=["set", "clear"],
            ),
            ToolParameterInfo(
                name="msg_id",
                param_type=ToolParamType.STRING,
                description="action=set 时，带图片的那条消息的 msg_id",
                required=False,
            ),
        ],
        chat_scope="private",
    )
    async def tool_background(self, action: str = "", msg_id: str = "", stream_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        chat_id, refused = await self._tool_chat(stream_id, "background")
        if refused:
            return refused
        if action not in ("set", "clear"):
            return {"success": False, "error": "action 只能是 set 或 clear"}
        if action == "set" and not msg_id:
            return {"success": False, "error": "设置背景要用 msg_id 指定一条带图片的消息"}
        args: Dict[str, Any] = {"chat_id": chat_id, "action": action}
        if action == "set":
            args["message_id"] = msg_id
        try:
            # 要先从 Photon 下载图片再上传，大图可能超过 20 秒
            await self._sidecar_call("background", args, timeout=BACKGROUND_TIMEOUT)
        except SidecarError as exc:
            if exc.code == "NO_IMAGE":
                return {"success": False, "error": "这条消息里没有图片，换一条带图片的消息"}
            return self._tool_failure("设置", exc)
        return {"success": True, "content": "已把聊天背景换成这张图片" if action == "set" else "已清除聊天背景"}

    @Tool(
        "imessage_share_contact",
        description=SHARE_CONTACT_DESCRIPTION,
        parameters=[],
        chat_scope="private",
    )
    async def tool_share_contact(self, stream_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        chat_id, refused = await self._tool_chat(stream_id, "contact_card")
        if refused:
            return refused
        try:
            await self._sidecar_call("share_contact", {"chat_id": chat_id})
        except SidecarError as exc:
            return self._tool_failure("分享", exc)
        return {
            "success": True,
            "content": "已分享。对方不会收到新消息，而是在聊天顶部你的名字下面看到「更新」，点了才会改成 Photon 账号设置的名字和头像",
        }

    # ── 贴贴纸：从 MaiBot 表情包库挑一张，贴在对方的消息上 ────────

    @Tool(
        "imessage_place_sticker",
        description=STICKER_DESCRIPTION,
        parameters=[
            ToolParameterInfo(name="msg_id", param_type=ToolParamType.STRING, description="要贴贴纸的那条消息的 msg_id"),
            ToolParameterInfo(name="emotion", param_type=ToolParamType.STRING, description="想表达的情绪，例如 开心、无语"),
        ],
        chat_scope="private",
    )
    async def tool_place_sticker(self, msg_id: str = "", emotion: str = "", stream_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        chat_id, refused = await self._tool_chat(stream_id, "stickers")
        if refused:
            return refused
        if self._media_dir is None:
            return {"success": False, "error": "iMessage 网关未就绪"}
        try:
            emoji = await self.ctx.emoji.get_by_description(emotion.strip() or "开心")
        except Exception as exc:  # noqa: BLE001 - 取表情包失败只告诉 AI
            self.ctx.logger.warning("iMessage 工具：取表情包失败 %s", exc)
            return {"success": False, "error": "这次没取到表情包"}
        if not isinstance(emoji, dict) or not emoji.get("base64"):
            return {"success": False, "error": f"表情包库里没有适合「{emotion}」的表情包，这次没贴成"}
        try:
            raw = base64.b64decode(str(emoji["base64"]), validate=False)
            data, extension = await asyncio.to_thread(prepare_sticker, raw)
        except (binascii.Error, ValueError, OSError) as exc:
            self.ctx.logger.warning("iMessage 工具：表情包图片处理失败 %s", exc)
            return {"success": False, "error": "这张表情包的图片处理失败，这次没贴成"}
        path = self._media_dir / f"sticker-{secrets.token_hex(8)}{extension}"
        await asyncio.to_thread(path.write_bytes, data)
        try:
            await self._sidecar_call(
                "place_sticker",
                {"chat_id": chat_id, "message_id": msg_id, "path": str(path), "name": f"sticker{extension}"},
                timeout=BACKGROUND_TIMEOUT,
            )
        except SidecarError as exc:
            return self._tool_failure("贴", exc)
        finally:
            path.unlink(missing_ok=True)
        described = str(emoji.get("description") or emoji.get("emotion") or emotion).strip()
        return {"success": True, "content": f"已在这条消息上贴了一张贴纸（{described}）"}
