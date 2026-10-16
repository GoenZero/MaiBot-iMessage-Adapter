"""
MaiBot iMessage Adapter — Main Program

侧车模式：Python 插件启动一个 Node.js 子进程（sidecar/dist/sidecar.mjs），通过
标准输入输出与它对话；侧车用 spectrum-ts 连接 Photon 云端收发 iMessage。

原作者 Galeros（mayan613）；0.2.0 起由 AI 维护重写，见 CHANGELOG.md。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional

from maibot_sdk import Command, HookHandler, MaiBotPlugin, MessageGateway
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

import asyncio
import contextlib
import copy
import functools
import os
import shutil
import time

from .config import PLUGIN_VERSION, IMessageAdapterConfig
from .core.bridge import SidecarError, SidecarSupervisor
from .core.capabilities import all_tool_names, build_brief, capability, disabled_tool_names, status_lines
from .core.events import describe_control_event
from .core.inbound import build_maibot_message
from .core.media import load_image_file
from .core.node_runtime import resolve_node
from .core.outbound import apply_link_previews, build_outbound_plan, looks_like_markdown, resolve_target
from .core.planner import adjust_planner_request
from .core.sessions import SessionDirectory
from .core.typing import TypingController
from .tools import ACTION_TIMEOUT, IMessageToolsMixin

if TYPE_CHECKING:
    from maibot_sdk import PluginConfigBase

GATEWAY_NAME = "imessage"
PLATFORM = "imessage"
SIDECAR_SCRIPT = Path(__file__).resolve().parent / "sidecar" / "dist" / "sidecar.mjs"
# 侧车发一条消息的最长等待（MaiBot 给网关的上限是 60 秒）
SEND_TIMEOUT = 45.0
# 收到消息时网关还没上报就绪，最多等多久
GATEWAY_READY_WAIT = 10.0
# 语音识别的最长等待：收消息是逐条排队处理的，识别太慢会卡住后面的消息
TRANSCRIBE_TIMEOUT = 30.0
_STATE_TEXT = {
    "stopped": "⏹ 未运行",
    "starting": "⏳ 正在启动",
    "ready": "✅ 已连接",
    "waiting_retry": "🔁 等待重连",
    "auth_failed": "❌ Photon 认证失败（请检查项目 ID 和密钥）",
}


def normalize_account(raw: str) -> str:
    """把号码里的空格、横线、括号去掉（"+1 (415) 555-0100" → "+14155550100"）；邮箱只转小写。"""
    value = raw.strip()
    if not value:
        return ""
    if "@" in value:
        return value.lower()
    digits = "".join(ch for ch in value if ch.isdigit())
    return ("+" if value.startswith("+") else "") + digits


def _discard_plan(building: "asyncio.Future[Any]") -> None:
    """发送取消后才处理完的出站计划：删掉它写出的临时附件。"""
    if building.cancelled() or building.exception() is not None:
        return
    building.result().cleanup()

class IMessageAdapterPlugin(IMessageToolsMixin, MaiBotPlugin):
    """iMessage 消息网关适配器插件。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = IMessageAdapterConfig

    def __init__(self) -> None:
        super().__init__()
        self._supervisor: Optional[SidecarSupervisor] = None
        self._media_dir: Optional[Path] = None
        # 对方用户 ID（手机号/邮箱）→ iMessage 对话编号
        self._chat_by_user: Dict[str, str] = {}
        self._last_error: str = ""
        # 日志里要遮蔽的密钥（包括改配置前的旧密钥）
        self._secrets: set[str] = set()
        self._sessions = SessionDirectory(self._fetch_imessage_streams)
        self._background: set[asyncio.Task[Any]] = set()
        self._typing: Optional[TypingController] = None
        # 每个对话里对方最新一条消息的编号，以及我们最近一次引用过的编号（「总是引用」档用）
        self._last_inbound: Dict[str, str] = {}
        self._last_quoted: Dict[str, str] = {}
        # 侧车进程在，但 SDK 与 Photon 的消息流断了、正在自动重连时，记下开始时间
        self._link_down_since: Optional[float] = None
        self._last_outage: str = ""
        # 侧车启动时的连接相关配置，以及最近一次上报给 MaiBot 的机器人号码
        self._active_settings: Optional[Dict[str, Any]] = None
        self._reported_account: Optional[str] = None
        # 已经告诉 MaiBot「网关就绪」：之前交给 MaiBot 的消息会被拒收
        self._gateway_ready = asyncio.Event()
        # 按对方号码记：AI 最近一次发消息的时间，以及最近一次告诉 AI「对方已读」的时间
        self._last_sent_at: Dict[str, float] = {}
        self._read_noted_at: Dict[str, float] = {}
        # 每个对话一把发送锁，保证消息按 MaiBot 交来的顺序发出：[锁, 正在用它的发送数]
        self._send_locks: Dict[str, list] = {}
        # 工具要用的记录（AI 发出的消息组、点过的回应）；和侧车的消息缓存同样大小
        self._init_tool_state(500)

    """═══════════════════════════════════════════════
    生命周期
    ═══════════════════════════════════════════════"""

    async def on_load(self) -> None:
        await self._restart_connection_if_needed()

    async def on_unload(self) -> None:
        await self._stop_connection()
        self.ctx.logger.info("iMessage 适配器已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        if scope != "self":
            return
        self.ctx.logger.info("iMessage 适配器收到配置更新，正在应用…")
        # 注意：MaiBot 在调用这里之前就已经把新配置注入插件了，所以不能拿「注入前」做对比，
        # 只能和侧车启动时记下的配置（_active_settings）比较
        self.set_plugin_config(config_data)
        if version:
            self.ctx.logger.debug("iMessage 适配器配置版本: %s", version)
        if self._supervisor is None or self._connection_settings() != self._active_settings:
            await self._restart_connection_if_needed()
            return
        # 只改了能力开关、图片处理、机器人号码等：立即生效，不必重启侧车
        if self._bot_account() != self._reported_account and self._supervisor.is_ready:
            await self._on_ready_change(True)
            self.ctx.logger.info("已向 MaiBot 重新上报机器人号码：%s", self._bot_account() or "（已清空）")
        self.ctx.logger.info("iMessage 适配器配置已生效（无需重连）")

    def _connection_settings(self) -> Dict[str, Any]:
        """会影响侧车进程的配置；只有这些变了才需要重启侧车。"""
        config = copy.deepcopy(self.get_plugin_config_data())
        capabilities = config.get("capabilities", {}) if isinstance(config.get("capabilities"), dict) else {}
        photon = config.get("photon") if isinstance(config.get("photon"), dict) else {}
        # 机器人号码只用于向 MaiBot 上报，改它不需要重启侧车
        photon.pop("bot_number", None)
        return {
            "enabled": self.config.plugin.enabled,
            "photon": photon,
            "bridge": config.get("bridge"),
            # 读取文本文件由侧车决定是否下载
            "read_text_files": capabilities.get("read_text_files"),
            # 语音转文字开启时，侧车才下载语音
            "transcribe_voice": self.config.voice.transcribe,
        }

    """═══════════════════════════════════════════════
    连接管理
    ═══════════════════════════════════════════════"""

    def _redact(self, text: str) -> str:
        # 记住用过的每一个密钥：改了密钥以后，旧侧车留下的日志里也可能有旧密钥
        secret = self.config.photon.project_secret
        if secret and len(secret) >= 4:
            self._secrets.add(secret)
        for known in self._secrets:
            text = text.replace(known, "***")
        return text

    async def _restart_connection_if_needed(self) -> None:
        await self._stop_connection()
        self._active_settings = self._connection_settings()
        if not self.config.plugin.should_connect():
            self.ctx.logger.info("iMessage 适配器保持空闲，因为插件未启用")
            return
        if not self.config.photon.project_id or not self.config.photon.project_secret:
            self.ctx.logger.warning("iMessage 适配器未启动：请先在 WebUI 填写 Photon 项目 ID 和密钥")
            return
        if not SIDECAR_SCRIPT.exists():
            self.ctx.logger.error("缺少侧车文件 %s，插件安装不完整", SIDECAR_SCRIPT)
            return

        # 附件临时目录放在 MaiBot 的运行时目录（容器内部、不持久），每次启动清空
        self._media_dir = Path(self.ctx.paths.runtime_dir) / "media"
        shutil.rmtree(self._media_dir, ignore_errors=True)
        self._media_dir.mkdir(parents=True, exist_ok=True)

        self._typing = TypingController(self._send_typing, self.ctx.logger)
        self._supervisor = SidecarSupervisor(
            logger=self.ctx.logger,
            on_event=self._on_sidecar_event,
            on_ready_change=self._on_ready_change,
            redact=self._redact,
        )
        self._supervisor.start(
            self._launch_args,
            fast_retries=self.config.bridge.max_retries,
            retry_interval=self.config.bridge.retry_interval,
        )

    async def _stop_connection(self) -> None:
        typing, self._typing = self._typing, None
        if typing is not None:
            await typing.close()
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            await supervisor.stop()

    async def _launch_args(self) -> tuple[str, Path, Dict[str, str]]:
        """每次启动侧车前调用：找 node，组装环境变量。"""
        node, version = await resolve_node(self.config.bridge.node_path, self.ctx.logger)
        self.ctx.logger.info("使用 Node.js %s：%s", version, node)
        assert self._media_dir is not None
        env = {
            # 继承 MaiBot 的环境（代理、证书、时区等设置要对侧车同样生效）
            **os.environ,
            "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""),
            "PHOTON_PROJECT_ID": self.config.photon.project_id,
            "PHOTON_PROJECT_SECRET": self.config.photon.project_secret,
            "MEDIA_DIR": str(self._media_dir),
            "MAX_ATTACHMENT_BYTES": str(self.config.bridge.max_attachment_size_mb * 1024 * 1024),
            "HANDLE_CACHE_SIZE": str(self.config.bridge.handle_cache_size),
            "READ_TEXT_FILES": "1" if self.config.capabilities.read_text_files else "0",
            "TRANSCRIBE_VOICE": "1" if self.config.voice.transcribe else "0",
        }
        return node, SIDECAR_SCRIPT, env

    def _bot_account(self) -> str:
        """WebUI 填写的 Photon 号码，规范成 +国家代码数字（邮箱则转小写）。"""
        return normalize_account(self.config.photon.bot_number)

    async def _on_ready_change(self, ready: bool) -> None:
        self._link_down_since = None
        self._gateway_ready.clear()
        if ready:
            # 带上机器人账号：MaiBot 会把它记为本平台的机器人身份，不需要再手动配置备用账号
            self._reported_account = self._bot_account()
            if not self._reported_account:
                self.ctx.logger.warning(
                    "没有填写「Photon 号码」：MaiBot 1.3.6 及以后的版本会拒收所有 iMessage 消息，"
                    "请在插件配置「Photon 云端 → Photon 号码」里填写"
                )
            await self.ctx.gateway.update_state(
                GATEWAY_NAME,
                ready=True,
                platform=PLATFORM,
                account_id=self._reported_account,
                metadata={"protocol": "photon"},
            )
            self._gateway_ready.set()
        else:
            await self.ctx.gateway.update_state(GATEWAY_NAME, ready=False)

    async def _fetch_imessage_streams(self) -> Any:
        return await self.ctx.chat.get_all_streams(platform=PLATFORM)

    async def _imessage_user_for(self, session_id: str) -> Optional[str]:
        """MaiBot 聊天流 ID → 对方的 iMessage 地址；不是 iMessage 私聊返回 None。"""
        try:
            return await self._sessions.user_for(session_id)
        except Exception as exc:
            self.ctx.logger.warning("查询聊天流失败：%s", exc)
            return None

    async def _chat_for_stream(self, stream_id: str) -> Optional[str]:
        """MaiBot 聊天流 ID → iMessage 对话编号。"""
        user = await self._imessage_user_for(stream_id)
        return self._chat_by_user.get(user, user) if user else None

    def _spawn(self, coro: Any) -> None:
        """后台执行一个不影响聊天主流程的动作（输入中、已读等）。"""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    """═══════════════════════════════════════════════
    让 AI 知道：工具可见性与能力说明
    ═══════════════════════════════════════════════"""

    @HookHandler(
        "maisaka.planner.before_request",
        name="imessage_planner_context",
        description="iMessage 聊天里插入能力说明、隐藏已关闭的能力；其他平台的聊天隐藏 iMessage 工具",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=5000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_planner_request(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        session_id = str(kwargs.get("session_id") or "")
        active = self._supervisor is not None
        is_imessage = active and await self._imessage_user_for(session_id) is not None
        if is_imessage:
            hidden = disabled_tool_names(self.config)
            brief = build_brief(self.config)
        else:
            hidden, brief = all_tool_names(), ""
        modified = adjust_planner_request(
            kwargs,
            is_imessage=is_imessage,
            hidden_tools=hidden,
            reply_mode=self.config.capabilities.reply_mode,
            brief=brief,
        )
        if modified is None:
            return None
        return {"action": "continue", "modified_kwargs": modified}

    """═══════════════════════════════════════════════
    入站：iMessage → MaiBot
    ═══════════════════════════════════════════════"""

    async def _on_sidecar_event(self, event: str, data: Dict[str, Any]) -> None:
        if event == "message":
            await self._handle_inbound_message(data)
        elif event == "control":
            self._spawn(self._handle_control_event(data))
        elif event == "fatal":
            self._last_error = f"[{data.get('code')}] {data.get('message')}"
        elif event == "link":
            self._on_link_state(str(data.get("state", "")), str(data.get("detail", "")))

    def _on_link_state(self, state: str, detail: str) -> None:
        """SDK 与 Photon 之间的消息流中断 / 恢复（侧车进程本身没退出）。"""
        if state == "reconnecting" and self._link_down_since is None:
            self._link_down_since = time.time()
            self._last_error = detail
            self.ctx.logger.warning("与 Photon 的连接中断，正在自动重连（侧车无需重启）")
        elif state == "ok" and self._link_down_since is not None:
            seconds = int(time.time() - self._link_down_since)
            started = time.strftime("%H:%M:%S", time.localtime(self._link_down_since))
            self._last_outage = f"{started} 起中断约 {seconds} 秒，已自动恢复"
            self._link_down_since = None
            self.ctx.logger.info("与 Photon 的连接已恢复（中断约 %d 秒）", seconds)

    async def _transcribe_voice_parts(self, data: Dict[str, Any]) -> None:
        """侧车下载好的语音：交给 MaiBot 的语音识别转成文字。失败只记日志，AI 会得体地告诉对方没听清。"""
        parts = data.get("parts") if isinstance(data.get("parts"), list) else []
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "voice" or not part.get("path"):
                continue
            path = Path(str(part.pop("path")))
            try:
                audio = await asyncio.to_thread(path.read_bytes)
            except OSError as exc:
                self.ctx.logger.warning("读取语音临时文件失败：%s", exc)
                part["note"] = "download_failed"
                continue
            finally:
                path.unlink(missing_ok=True)
            if not self.config.voice.transcribe:
                continue
            try:
                result = await asyncio.wait_for(self.ctx.llm.transcribe_audio(audio), timeout=TRANSCRIBE_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - 识别失败（含超时）不影响聊天
                self.ctx.logger.warning("语音转文字失败：%s", exc)
                part["note"] = "transcribe_failed"
                continue
            text = str(result.get("text") or "").strip() if isinstance(result, dict) else ""
            if isinstance(result, dict) and result.get("success") and text:
                part["transcript"] = text
                self.ctx.logger.info("语音已转成文字（%d 字）", len(text))
            else:
                error = result.get("error") if isinstance(result, dict) else result
                self.ctx.logger.warning("语音转文字没有结果：%s（请检查 MaiBot 的语音识别模型配置）", error or "识别结果为空")
                part["note"] = "transcribe_failed"

    async def _handle_control_event(self, data: Dict[str, Any]) -> None:
        """对方点赞、投票选择、编辑、撤回、已读：以系统通知的形式写进 AI 的聊天记录（不唤醒 AI，下一轮能看到）。"""
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        kind = str(event.get("type", ""))
        chat_id = str(data.get("chat_id", ""))
        sender_id = str(data.get("sender_id", ""))
        self.ctx.logger.info("收到 iMessage 事件：%s", kind)
        if sender_id and chat_id:
            self._chat_by_user[sender_id] = chat_id

        previous_note: Optional[float] = None
        if kind in ("reaction", "reaction_removed", "poll_vote"):
            if not capability("inbound_reactions").enabled(self.config):
                return
        elif kind in ("edited", "unsent"):
            if not capability("inbound_edits").enabled(self.config):
                return
        elif kind == "read":
            # 同一批消息只提醒一次：上次提醒之后 AI 又发过消息，才再提醒。按对方号码记
            if not capability("inbound_read").enabled(self.config):
                return
            last_sent = self._last_sent_at.get(sender_id)
            previous_note = self._read_noted_at.get(sender_id, 0.0)
            if last_sent is None or previous_note >= last_sent:
                return
            # 先占上，避免几条已读同时到达时重复提醒；没告诉成 AI 时再退回去
            self._read_noted_at[sender_id] = time.time()

        text = describe_control_event(event)
        if text and await self._tell_ai(sender_id, text):
            return
        if previous_note is not None:
            self._read_noted_at[sender_id] = previous_note

    async def _tell_ai(self, sender_id: str, text: str) -> bool:
        """把一条系统通知写进和这个人的聊天记录。成功返回 True；失败只记日志。"""
        try:
            session_id = await self._sessions.session_for(sender_id, self._reported_account or "")
            if not session_id:
                self.ctx.logger.info("还没有和 %s 的聊天记录，事件不告诉 AI", sender_id)
                return False
            result = await self.ctx.maisaka.context.append(
                session_id,
                [{"type": "text", "data": text}],
                visible_text=text,
                source_kind="imessage_event",
            )
        except Exception as exc:  # noqa: BLE001 - 查找聊天流或写入失败都只记日志，不影响聊天
            self.ctx.logger.warning("把 iMessage 事件写进 AI 聊天记录失败：%s", exc)
            return False
        if isinstance(result, dict) and result.get("success") is False:
            self.ctx.logger.warning("把 iMessage 事件写进 AI 聊天记录失败：%s", result.get("error"))
            return False
        self.ctx.logger.info("已告诉 AI：%s", text)
        return True

    async def _handle_inbound_message(self, data: Dict[str, Any]) -> None:
        sender_id = str(data.get("sender_id", ""))
        chat_id = str(data.get("chat_id", ""))
        if sender_id and chat_id:
            self._chat_by_user[sender_id] = chat_id

        await self._transcribe_voice_parts(data)
        loader = functools.partial(load_image_file, max_edge=max(0, self.config.media.image_max_edge))
        message = await asyncio.to_thread(build_maibot_message, data, loader)
        # 侧车一连上，积压的消息就会跟着到，可能比「网关就绪」的上报还早；先等上报完成，否则 MaiBot 会拒收
        if not self._gateway_ready.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._gateway_ready.wait(), timeout=GATEWAY_READY_WAIT)
        # MaiBot 1.3.6 起，入站消息要写明属于哪个机器人账号（消息顶层 account_id），
        # 且必须和网关上报的账号一致；没写时 1.3.9 每条消息都记一条「旧版格式」警告，以后的版本会直接拒收
        if self._reported_account:
            message["account_id"] = self._reported_account
        message_id = str(data.get("message_id", ""))
        try:
            accepted = await self.ctx.gateway.route_message(
                gateway_name=GATEWAY_NAME,
                message=message,
                external_message_id=message_id,
            )
        except Exception as exc:
            self.ctx.logger.error("注入入站消息失败：%s", exc)
            return
        if not accepted:
            self.ctx.logger.debug("MaiBot 未接收入站消息：%s", message_id)
            return
        # 贴在消息上的贴纸，编号是侧车拼出来的，Photon 不认识：引用、标已读都用被贴的那条消息
        handle_id = message_id
        if data.get("quotable") is False:
            reply_to = data.get("reply_to") if isinstance(data.get("reply_to"), dict) else {}
            handle_id = str(reply_to.get("message_id") or "")
        if chat_id and handle_id:
            self._last_inbound[chat_id] = handle_id
        if self.config.capabilities.read_receipts and chat_id and handle_id:
            self._spawn(self._mark_read(chat_id, handle_id))

    async def _send_typing(self, chat_id: str, state: str) -> None:
        supervisor = self._supervisor
        if supervisor is not None:
            await supervisor.request("typing", {"chat_id": chat_id, "state": state}, timeout=ACTION_TIMEOUT)

    @HookHandler(
        "maisaka.replyer.before_request",
        name="imessage_typing_start",
        description="AI 开始生成回复时，在 iMessage 里显示「正在输入」",
        mode=HookMode.OBSERVE,
        timeout_ms=5000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_replyer_request(self, **kwargs: Any) -> None:
        typing = self._typing
        if typing is None or not self.config.capabilities.typing_indicator:
            return None
        chat_id = await self._chat_for_stream(str(kwargs.get("session_id") or ""))
        if chat_id:
            await typing.start(chat_id)
        return None

    @HookHandler(
        "send_service.before_send",
        name="imessage_quote_policy",
        description="引用回复设为「关闭」时，让 MaiBot 发送和记录 iMessage 消息时都不带引用",
        mode=HookMode.BLOCKING,
        timeout_ms=3000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_before_send(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """关闭档下把 set_reply 改成 False：消息不带引用，MaiBot 的聊天记录里也不会写成「引用了某句」，
        AI 回头看历史时不会误以为自己引用过。"""
        if self.config.capabilities.reply_mode != "off" or not kwargs.get("set_reply"):
            return None
        message = kwargs.get("message")
        if not isinstance(message, dict) or str(message.get("platform", "")) != PLATFORM:
            return None
        return {"action": "continue", "modified_kwargs": {**kwargs, "set_reply": False}}

    async def _mark_read(self, chat_id: str, message_id: str) -> None:
        """自动发已读：失败只记日志。"""
        supervisor = self._supervisor
        if supervisor is None:
            return
        try:
            await supervisor.request("mark_read", {"chat_id": chat_id, "message_id": message_id}, timeout=ACTION_TIMEOUT)
        except SidecarError as exc:
            self.ctx.logger.warning("发送已读失败 %s", exc)

    """═══════════════════════════════════════════════
    出站：MaiBot → iMessage
    ═══════════════════════════════════════════════"""

    @MessageGateway(
        route_type="duplex",
        name=GATEWAY_NAME,
        platform=PLATFORM,
        protocol="photon",
        description="iMessage 消息收发网关（通过 Photon Spectrum 云端）",
    )
    async def send_to_imessage(
        self,
        message: dict[str, Any],
        route: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """出站入口：等侧车真正发出后，把 iMessage 消息编号交还 MaiBot。"""
        del route, metadata, kwargs
        supervisor = self._supervisor
        if supervisor is None or not supervisor.is_ready or self._media_dir is None:
            return {"success": False, "error": "iMessage 网关未就绪"}

        target = resolve_target(message)
        if not target:
            return {"success": False, "error": "缺少目标用户"}
        chat_id = self._chat_by_user.get(target, target)
        async with self._send_turn(chat_id):
            return await self._send_in_turn(supervisor, message, target, chat_id, self._media_dir)

    @contextlib.asynccontextmanager
    async def _send_turn(self, chat_id: str) -> Any:
        """同一个对话的发送一条一条来：带图片的消息要先在后台处理图片，后交来的文字不能抢先发出。"""
        entry = self._send_locks.get(chat_id)
        if entry is None:
            entry = self._send_locks[chat_id] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._send_locks.pop(chat_id, None)

    async def _send_in_turn(
        self, supervisor: SidecarSupervisor, message: dict[str, Any], target: str, chat_id: str, media_dir: Path
    ) -> dict[str, Any]:
        max_bytes = self.config.bridge.max_attachment_size_mb * 1024 * 1024
        building = asyncio.ensure_future(asyncio.to_thread(build_outbound_plan, message, media_dir, max_bytes))
        try:
            plan = await asyncio.shield(building)
        except asyncio.CancelledError:
            # 发送被取消时，后台线程还在写临时附件；等它写完再删掉，不留孤儿文件
            building.add_done_callback(_discard_plan)
            raise
        for skipped in plan.skipped:
            self.ctx.logger.warning("出站消息中的「%s」暂不支持发送，已跳过", skipped)
        if not plan.parts:
            return {"success": False, "error": "没有可发送的内容"}

        if self.config.capabilities.markdown:
            for part in plan.parts:
                if part["type"] == "text" and looks_like_markdown(part["text"]):
                    part["markdown"] = True
        if self.config.capabilities.link_preview:
            apply_link_previews(plan.parts)
        reply_to = self._choose_reply_target(chat_id, plan.reply_to)
        args: Dict[str, Any] = {"chat_id": chat_id, "parts": plan.parts}
        if reply_to:
            args["reply_to"] = reply_to
        try:
            result = await supervisor.request("send", args, timeout=SEND_TIMEOUT)
        except SidecarError as exc:
            self.ctx.logger.error("发送 iMessage 失败 %s", self._redact(str(exc)))
            return {"success": False, "error": exc.message}
        finally:
            plan.cleanup()
            # 发出（或失败）后关掉「正在输入」
            if self._typing is not None:
                self._spawn(self._typing.finish(chat_id))

        if reply_to and result.get("quoted"):
            self._last_quoted[chat_id] = reply_to
        message_ids = [str(x) for x in result.get("message_ids", []) if x]
        if result.get("partial"):
            self.ctx.logger.warning("消息只发出了一部分：%s", message_ids)
        self._remember_sent(message_ids)
        self._last_sent_at[target] = time.time()
        return {"success": True, "external_message_id": message_ids[0] if message_ids else None}

    def _choose_reply_target(self, chat_id: str, requested: Optional[str]) -> Optional[str]:
        """按引用档位决定这条回复引用哪条消息。

        - auto：照 MaiBot 给的（AI 在 reply 工具里明确选了 set_quote 才会有）
        - always：MaiBot 没给时，引用对方最新一条；同一条只引用一次，避免分段回复每段都引用
        - off：一律不引用
        """
        mode = self.config.capabilities.reply_mode
        if mode == "off":
            return None
        if mode == "always" and not requested:
            latest = self._last_inbound.get(chat_id)
            if latest and self._last_quoted.get(chat_id) != latest:
                return latest
            return None
        return requested

    """═══════════════════════════════════════════════
    管理命令
    ═══════════════════════════════════════════════"""

    @Command(
        "imessage_status",
        description="查看 iMessage 适配器运行状态",
        pattern=r"^/imessage_status$",
    )
    async def handle_status(self, stream_id: str = "", **kwargs: Any) -> tuple:
        """返回侧车状态、版本、重启次数、最近错误等。"""
        del kwargs
        lines = [f"📱 iMessage 适配器 v{PLUGIN_VERSION}"]
        supervisor = self._supervisor
        if supervisor is None:
            lines.append("状态：⏹ 未运行（插件未启用或未填写 Photon 凭证）")
        else:
            status = supervisor.status
            state_text = _STATE_TEXT.get(status.state, status.state)
            if status.state == "ready" and self._link_down_since is not None:
                since = time.strftime("%H:%M:%S", time.localtime(self._link_down_since))
                state_text = f"🔁 与 Photon 的连接中断，正在自动重连（{since} 起）"
            lines.append(f"状态：{state_text}")
            if status.pid:
                lines.append(f"侧车 PID：{status.pid}")
            if status.ready_since:
                minutes = int((time.time() - status.ready_since) // 60)
                lines.append(f"已连续运行：{minutes} 分钟")
            if status.spectrum_version:
                lines.append(f"spectrum-ts {status.spectrum_version} · Node {status.node_version}")
            lines.append(f"累计重启：{status.restarts} 次")
            if status.next_retry_at:
                lines.append(f"下次重连：{max(0, int(status.next_retry_at - time.time()))} 秒后")
            if self._last_outage:
                lines.append(f"最近一次中断：{self._last_outage}")
            last_error = status.last_error or self._last_error
            if last_error:
                lines.append(f"最近错误：{self._redact(last_error)[:200]}")
        lines.append(f"Photon 项目：{self.config.photon.project_id or '未配置'}")
        lines.append(f"机器人号码：{self._bot_account() or '未填写（MaiBot 1.3.6 起必须填写，否则收不到消息）'}")
        lines.append("能力：")
        lines.extend(f"  {line}" for line in status_lines(self.config))
        await self.ctx.send.text("\n".join(lines), stream_id)
        return True, "状态已显示", True

    @Command(
        "imessage_reconnect",
        description="手动重连 iMessage 适配器",
        pattern=r"^/imessage_reconnect$",
    )
    async def handle_reconnect(self, stream_id: str = "", **kwargs: Any) -> tuple:
        """手动重启侧车（认证失败停下后，也可以用它重新尝试）。"""
        del kwargs
        supervisor = self._supervisor
        if supervisor is not None and supervisor.status.state not in ("auth_failed", "stopped"):
            await supervisor.restart()
        else:
            await self._restart_connection_if_needed()
        with contextlib.suppress(Exception):
            await self.ctx.send.text("🔄 iMessage 适配器已触发重连", stream_id)
        return True, "已重连", True


def create_plugin() -> IMessageAdapterPlugin:
    """MaiBot 插件工厂函数。"""
    return IMessageAdapterPlugin()
