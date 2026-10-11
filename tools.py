"""给 AI 用的 iMessage 工具。

写成混入类，由 plugin.py 的插件类继承（SDK 会扫描插件实例上所有带 @Tool 的方法）。
每个工具都先查能力注册表：能力关闭时，工具已经从 AI 的工具列表里拿掉了，这里再挡一次。

失败时一律返回 success=False 和一句 AI 能看懂的原因（例如「找不到这条消息，这次没点成」），
不抛异常，不影响聊天。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from maibot_sdk import Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .core.bridge import SidecarError
from .core.capabilities import capability
from .core.lru import LruDict
from .core.outbound import looks_like_markdown

if TYPE_CHECKING:
    from .config import IMessageAdapterConfig
    from .core.bridge import SidecarSupervisor

ACTION_TIMEOUT = 20.0

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
    "只能改你自己 15 分钟内发的文字消息，每条最多改 5 次。"
)
UNSEND_DESCRIPTION = (
    "撤回你自己发出的一条 iMessage 消息，对方那边这条消息会消失（显示「撤回了一条消息」）。"
    "只在发错了对象、发错了内容，或对方要你撤回时用；平时不要用。"
    "只能撤回你自己 2 分钟内发的消息。要撤回点赞，请用 imessage_react 并填 remove=true。"
)


class IMessageToolsMixin:
    """AI 工具。依赖插件类提供的 config、ctx、_supervisor、_chat_for_stream。"""

    if TYPE_CHECKING:
        config: IMessageAdapterConfig
        ctx: Any
        _supervisor: Optional[SidecarSupervisor]

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

    async def _sidecar_call(self, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        assert self._supervisor is not None
        return await self._supervisor.request(op, args, timeout=ACTION_TIMEOUT)

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
        if remove:
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
        if self.config.capabilities.markdown and looks_like_markdown(text):
            args["markdown"] = True
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
