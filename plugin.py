"""
MaiBot iMessage Adapter — Main Program

侧车模式：Python 插件启动一个 Node.js 子进程（sidecar/dist/sidecar.mjs），通过
标准输入输出与它对话；侧车用 spectrum-ts 连接 Photon 云端收发 iMessage。

原作者 Galeros（mayan613）；0.2.0 起由 AI 维护重写，见 CHANGELOG.md。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional

from maibot_sdk import Command, MaiBotPlugin, MessageGateway

import asyncio
import contextlib
import functools
import os
import shutil
import time

from .config import PLUGIN_VERSION, IMessageAdapterConfig
from .core.bridge import SidecarError, SidecarSupervisor
from .core.inbound import build_maibot_message
from .core.media import load_image_file
from .core.node_runtime import resolve_node
from .core.outbound import build_outbound_plan, resolve_target

if TYPE_CHECKING:
    from maibot_sdk import PluginConfigBase

GATEWAY_NAME = "imessage"
PLATFORM = "imessage"
SIDECAR_SCRIPT = Path(__file__).resolve().parent / "sidecar" / "dist" / "sidecar.mjs"
# 侧车发一条消息的最长等待（MaiBot 给网关的上限是 60 秒）
SEND_TIMEOUT = 45.0

_STATE_TEXT = {
    "stopped": "⏹ 未运行",
    "starting": "⏳ 正在启动",
    "ready": "✅ 已连接",
    "waiting_retry": "🔁 等待重连",
    "auth_failed": "❌ Photon 认证失败（请检查项目 ID 和密钥）",
}


class IMessageAdapterPlugin(MaiBotPlugin):
    """iMessage 消息网关适配器插件。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = IMessageAdapterConfig

    def __init__(self) -> None:
        super().__init__()
        self._supervisor: Optional[SidecarSupervisor] = None
        self._media_dir: Optional[Path] = None
        # 对方用户 ID（手机号/邮箱）→ iMessage 对话编号
        self._chat_by_user: Dict[str, str] = {}
        self._last_error: str = ""

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
        self.set_plugin_config(config_data)
        if version:
            self.ctx.logger.debug("iMessage 适配器配置版本: %s", version)
        await self._restart_connection_if_needed()

    """═══════════════════════════════════════════════
    连接管理
    ═══════════════════════════════════════════════"""

    def _redact(self, text: str) -> str:
        secret = self.config.photon.project_secret
        return text.replace(secret, "***") if secret and len(secret) >= 4 else text

    async def _restart_connection_if_needed(self) -> None:
        await self._stop_connection()
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
        }
        return node, SIDECAR_SCRIPT, env

    async def _on_ready_change(self, ready: bool) -> None:
        if ready:
            await self.ctx.gateway.update_state(GATEWAY_NAME, ready=True, platform=PLATFORM, metadata={"protocol": "photon"})
        else:
            await self.ctx.gateway.update_state(GATEWAY_NAME, ready=False)

    """═══════════════════════════════════════════════
    入站：iMessage → MaiBot
    ═══════════════════════════════════════════════"""

    async def _on_sidecar_event(self, event: str, data: Dict[str, Any]) -> None:
        if event == "message":
            await self._handle_inbound_message(data)
        elif event == "control":
            event_info = data.get("event") if isinstance(data.get("event"), dict) else {}
            self.ctx.logger.info("收到 iMessage 事件：%s", event_info.get("type"))
        elif event == "fatal":
            self._last_error = f"[{data.get('code')}] {data.get('message')}"

    async def _handle_inbound_message(self, data: Dict[str, Any]) -> None:
        sender_id = str(data.get("sender_id", ""))
        chat_id = str(data.get("chat_id", ""))
        if sender_id and chat_id:
            self._chat_by_user[sender_id] = chat_id

        loader = functools.partial(load_image_file, max_edge=max(0, self.config.media.image_max_edge))
        message = await asyncio.to_thread(build_maibot_message, data, loader)
        message_id = str(data.get("message_id", ""))
        try:
            accepted = await self.ctx.gateway.route_message(
                gateway_name=GATEWAY_NAME,
                message=message,
                external_message_id=message_id,
            )
            if not accepted:
                self.ctx.logger.debug("MaiBot 未接收入站消息：%s", message_id)
        except Exception as exc:
            self.ctx.logger.error("注入入站消息失败：%s", exc)

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

        max_bytes = self.config.bridge.max_attachment_size_mb * 1024 * 1024
        plan = await asyncio.to_thread(build_outbound_plan, message, self._media_dir, max_bytes)
        for skipped in plan.skipped:
            self.ctx.logger.warning("出站消息中的「%s」暂不支持发送，已跳过", skipped)
        if not plan.parts:
            return {"success": False, "error": "没有可发送的内容"}

        args: Dict[str, Any] = {"chat_id": chat_id, "parts": plan.parts}
        if plan.reply_to:
            args["reply_to"] = plan.reply_to
        try:
            result = await supervisor.request("send", args, timeout=SEND_TIMEOUT)
        except SidecarError as exc:
            self.ctx.logger.error("发送 iMessage 失败 %s", self._redact(str(exc)))
            return {"success": False, "error": exc.message}
        finally:
            plan.cleanup()

        message_ids = [str(x) for x in result.get("message_ids", []) if x]
        if result.get("partial"):
            self.ctx.logger.warning("消息只发出了一部分：%s", message_ids)
        return {"success": True, "external_message_id": message_ids[0] if message_ids else None}

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
            lines.append(f"状态：{_STATE_TEXT.get(status.state, status.state)}")
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
            last_error = status.last_error or self._last_error
            if last_error:
                lines.append(f"最近错误：{self._redact(last_error)[:200]}")
        lines.append(f"Photon 项目：{self.config.photon.project_id or '未配置'}")
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
