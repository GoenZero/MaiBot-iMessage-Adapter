"""与侧车进程通信的桥。

侧车是一个 Node.js 子进程，Python 通过它的标准输入输出说话（协议见
``sidecar/src/protocol.ts``）：

- 发请求：往 stdin 写一行 ``{"id": 1, "op": "send", "args": {...}}``
- 收回执：stdout 上带 ``"imsg": 1`` 标记、``"t": "res"`` 的行，按 id 对上请求
- 收事件：stdout 上 ``"t": "evt"`` 的行（ready / message / control / fatal）
- 侧车日志走 stderr，格式 ``LEVEL 内容``，原样转到 MaiBot 日志

``SidecarSupervisor`` 负责启动、崩溃后自动重启（指数退避，永不放弃，认证失败除外）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

import asyncio
import contextlib
import itertools
import json
import logging
import time

PROTOCOL_VERSION = 1
# 一行协议最长 16 MB（附件走文件，这里只是保险）
_LINE_LIMIT = 16 * 1024 * 1024
# 侧车启动后多久内必须报告 ready
_READY_TIMEOUT = 60.0
# 正常运行超过这个时长，就把连续失败计数清零
_STABLE_AFTER = 60.0
_MAX_BACKOFF = 300.0
# 认证失败的退出码：配置不改就不重试
EXIT_AUTH_FAILED = 2

EventHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
StateHandler = Callable[[bool], Awaitable[None]]


class SidecarError(Exception):
    """侧车返回的失败，或侧车不可用。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


@dataclass
class SidecarStatus:
    """给 /imessage_status 看的运行状态。"""

    state: str = "stopped"  # stopped / starting / ready / waiting_retry / auth_failed
    pid: Optional[int] = None
    node_version: str = ""
    spectrum_version: str = ""
    restarts: int = 0
    consecutive_failures: int = 0
    last_error: str = ""
    ready_since: Optional[float] = None
    next_retry_at: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class SidecarProcess:
    """一次侧车进程的生命周期：启动、收发协议、退出。"""

    def __init__(self, logger: logging.Logger, on_event: EventHandler, redact: Callable[[str], str]) -> None:
        self._logger = logger
        self._on_event = on_event
        self._redact = redact
        self._process: Optional[asyncio.subprocess.Process] = None
        self._pending: Dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._write_lock = asyncio.Lock()
        self._events: asyncio.Queue[Optional[tuple[str, Dict[str, Any]]]] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self.ready = asyncio.Event()
        self.ready_info: Dict[str, Any] = {}
        self.fatal: Optional[Dict[str, Any]] = None

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process is not None else None

    async def start(self, node: str, script: Path, env: Mapping[str, str]) -> None:
        self._process = await asyncio.create_subprocess_exec(
            node,
            str(script),
            cwd=str(script.parent),
            env=dict(env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_LINE_LIMIT,
        )
        self._tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
            asyncio.create_task(self._dispatch_events()),
        ]

    async def wait(self) -> int:
        """等待进程退出，返回退出码，并让所有未完成的请求失败。"""
        assert self._process is not None
        code = await self._process.wait()
        # 先把剩下的输出读完，再收尾
        for task in self._tasks[:2]:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=5.0)
        self._fail_pending(SidecarError("SIDECAR_EXITED", f"侧车已退出（退出码 {code}）"))
        # 已经收到的消息照常处理完：分发任务在后台继续跑，处理完积压就自己结束。
        # 不等它，也不取消它（以前等 10 秒就取消，积压多时会丢掉队尾的消息）
        await self._events.put(None)
        return code

    @property
    def dispatch_task(self) -> Optional[asyncio.Task[None]]:
        """处理收到的事件的后台任务；插件卸载时由监督器取消。"""
        return self._tasks[2] if len(self._tasks) > 2 else None

    async def request(self, op: str, args: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
        """发一个请求并等待回执；失败时抛出 SidecarError。"""
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise SidecarError("SIDECAR_DOWN", "侧车未运行")
        request_id = next(self._ids)
        future: asyncio.Future[Dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        line = json.dumps({"id": request_id, "op": op, "args": args or {}}, ensure_ascii=False) + "\n"
        stdin = process.stdin

        async def send_and_wait() -> Dict[str, Any]:
            async with self._write_lock:
                stdin.write(line.encode("utf-8"))
                await stdin.drain()
            return await future

        try:
            # 侧车不读输入时，写管道也会卡住，所以总时限要把写入也算进去
            return await asyncio.wait_for(send_and_wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise SidecarError("TIMEOUT", f"侧车 {timeout:.0f} 秒内没有回应 {op}") from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise SidecarError("SIDECAR_DOWN", "侧车管道已断开") from exc
        finally:
            self._pending.pop(request_id, None)

    async def terminate(self, grace: float = 5.0) -> None:
        """先请侧车自己退出，超时再强杀。"""
        process = self._process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(Exception):
            await self.request("shutdown", timeout=2.0)
        with contextlib.suppress(Exception):
            if process.stdin is not None:
                process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=grace)
        except asyncio.TimeoutError:
            self._logger.warning("侧车未在 %.0f 秒内退出，强制结束", grace)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    def _fail_pending(self, error: SidecarError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        while True:
            try:
                raw = await stream.readline()
            except ValueError:
                # 单行超长：丢弃这一行，但要记下来
                self._logger.error("侧车输出了一行超长内容，已丢弃")
                continue
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            frame: Any = None
            if text.startswith("{"):
                with contextlib.suppress(json.JSONDecodeError):
                    frame = json.loads(text)
            if not isinstance(frame, dict) or frame.get("imsg") != PROTOCOL_VERSION:
                # 不是协议帧（第三方库直接写了 stdout），当普通日志
                self._logger.info("[侧车] %s", self._redact(text))
                continue
            try:
                self._handle_frame(frame)
            except Exception:
                # 一帧格式不对不能让读取任务退出，否则之后的消息和回执都收不到了
                self._logger.exception("处理侧车输出的一帧时出错，已跳过")

    def _handle_frame(self, frame: Dict[str, Any]) -> None:
        if frame.get("t") == "res":
            request_id = frame.get("id")
            if not isinstance(request_id, int) or isinstance(request_id, bool):
                self._logger.warning("侧车回执的编号格式不对，已忽略")
                return
            future = self._pending.get(request_id)
            if future is None or future.done():
                return
            if frame.get("ok"):
                result = frame.get("result")
                future.set_result(result if isinstance(result, dict) else {})
            else:
                error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
                # 错误信息可能带出配置（例如密钥），先遮蔽再往外传，之后谁记日志都安全
                message = self._redact(str(error.get("message", "")))
                future.set_exception(SidecarError(self._redact(str(error.get("code", "UNKNOWN"))), message))
        elif frame.get("t") == "evt":
            event = str(frame.get("event", ""))
            data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
            if event == "ready":
                self.ready_info = data
                self.ready.set()
            elif event == "fatal":
                self.fatal = data
            self._events.put_nowait((event, data))

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        levels = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARN": logging.WARNING, "ERROR": logging.ERROR}
        stream = self._process.stderr
        while True:
            try:
                raw = await stream.readline()
            except ValueError:
                continue
            if not raw:
                return
            text = self._redact(raw.decode("utf-8", errors="replace").rstrip())
            if not text:
                continue
            head, _, rest = text.partition(" ")
            if head in levels:
                self._logger.log(levels[head], "[侧车] %s", rest)
            else:
                # SDK 自己的输出（已设为只输出 warn 以上）
                self._logger.warning("[侧车] %s", text)

    async def _dispatch_events(self) -> None:
        """按到达顺序逐个处理事件，保证同一会话里的消息顺序不乱。"""
        while True:
            item = await self._events.get()
            if item is None:
                return
            event, data = item
            try:
                await self._on_event(event, data)
            except Exception:
                self._logger.exception("处理侧车事件 %s 时出错", event)


class SidecarSupervisor:
    """管理侧车进程：启动、自动重启、对外提供 request()。"""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        on_event: EventHandler,
        on_ready_change: StateHandler,
        redact: Callable[[str], str],
    ) -> None:
        self._logger = logger
        self._on_event = on_event
        self._on_ready_change = on_ready_change
        self._redact = redact
        self._process: Optional[SidecarProcess] = None
        self._loop_task: Optional[asyncio.Task[None]] = None
        self._stopping = False
        self._restart_now = asyncio.Event()
        # 已退出的侧车留下的、还在处理积压消息的分发任务
        self._draining: set[asyncio.Task[None]] = set()
        self.status = SidecarStatus()

    @property
    def is_ready(self) -> bool:
        # 以侧车进程自己报告的 ready 为准：ready 之后紧接着到达的入站消息
        # （例如重连后 Photon 补发的）在处理时就能发已读、输入中
        process = self._process
        return process is not None and process.ready.is_set() and process.fatal is None

    async def request(self, op: str, args: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
        if not self.is_ready or self._process is None:
            raise SidecarError("NOT_READY", "iMessage 侧车尚未就绪")
        return await self._process.request(op, args, timeout=timeout)

    def start(
        self,
        launch: Callable[[], Awaitable[tuple[str, Path, Dict[str, str]]]],
        *,
        fast_retries: int,
        retry_interval: float,
    ) -> None:
        """开始运行监督循环（不阻塞）。launch 每次启动前调用，返回 (node, 脚本, 环境变量)。"""
        self._stopping = False
        self._loop_task = asyncio.create_task(self._run(launch, fast_retries, max(retry_interval, 1.0)))

    async def stop(self) -> None:
        self._stopping = True
        self._restart_now.set()
        if self._process is not None:
            await self._process.terminate()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._loop_task
            self._loop_task = None
        for task in list(self._draining):
            task.cancel()
        self._draining.clear()
        await self._set_ready(False)
        self.status.state = "stopped"
        self.status.pid = None

    async def restart(self) -> None:
        """手动重连：结束当前进程，监督循环会立刻重新拉起（只拉起一次）。"""
        self.status.consecutive_failures = 0
        self._restart_now.set()
        if self._process is not None:
            await self._process.terminate()

    async def _set_ready(self, ready: bool) -> None:
        try:
            await self._on_ready_change(ready)
        except Exception:
            self._logger.exception("上报网关状态失败")

    async def _run(
        self,
        launch: Callable[[], Awaitable[tuple[str, Path, Dict[str, str]]]],
        fast_retries: int,
        retry_interval: float,
    ) -> None:
        status = self.status
        while not self._stopping:
            self._restart_now.clear()
            status.state = "starting"
            started_at = time.monotonic()
            exit_code: Optional[int] = None
            process: Optional[SidecarProcess] = None
            try:
                node, script, env = await launch()
                process = SidecarProcess(self._logger, self._on_event, self._redact)
                self._process = process
                await process.start(node, script, env)
                status.pid = process.pid
                exit_task = asyncio.create_task(process.wait())
                ready_task = asyncio.create_task(process.ready.wait())
                done, _ = await asyncio.wait({exit_task, ready_task}, timeout=_READY_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
                if ready_task in done:
                    status.state = "ready"
                    status.ready_since = time.time()
                    status.node_version = str(process.ready_info.get("node", ""))
                    status.spectrum_version = str(process.ready_info.get("spectrum", ""))
                    status.last_error = ""
                    self._logger.info("iMessage 侧车已就绪（PID %s）", process.pid)
                    await self._set_ready(True)
                else:
                    ready_task.cancel()
                    if exit_task not in done:
                        status.last_error = f"侧车 {_READY_TIMEOUT:.0f} 秒内没有连上 Photon"
                        self._logger.error(status.last_error)
                        await process.terminate()
                exit_code = await exit_task
                if process.fatal:
                    status.last_error = f"[{process.fatal.get('code')}] {process.fatal.get('message')}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status.last_error = self._redact(str(exc))
                self._logger.error("启动侧车失败：%s", status.last_error)
            finally:
                dispatch = process.dispatch_task if process is not None else None
                if dispatch is not None and not dispatch.done():
                    self._draining.add(dispatch)
                    dispatch.add_done_callback(self._draining.discard)
                self._process = None
                status.pid = None
                status.ready_since = None
                await self._set_ready(False)

            if self._stopping:
                return
            if exit_code is not None:
                self._logger.warning("侧车已退出（退出码 %s）", exit_code)
            if exit_code == EXIT_AUTH_FAILED:
                status.state = "auth_failed"
                self._logger.error("Photon 认证失败，请在 WebUI 检查项目 ID 和密钥；修改配置后会自动重连")
                return

            if self._restart_now.is_set():
                # 手动重连：立刻重启，不计入失败
                status.restarts += 1
                continue

            if time.monotonic() - started_at >= _STABLE_AFTER:
                status.consecutive_failures = 0
            status.consecutive_failures += 1
            status.restarts += 1
            # 前几次快速重试，之后指数退避，最长 5 分钟一次，永不放弃
            if status.consecutive_failures <= fast_retries:
                delay = retry_interval
            else:
                # 指数先封顶再计算：断网好几天后连续失败上千次，2 的上千次方会溢出，监督循环就此退出
                exponent = min(status.consecutive_failures - fast_retries, 20)
                delay = min(retry_interval * 2**exponent, _MAX_BACKOFF)
            status.state = "waiting_retry"
            status.next_retry_at = time.time() + delay
            self._logger.info("%.0f 秒后重启侧车（连续失败 %d 次）", delay, status.consecutive_failures)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._restart_now.wait(), timeout=delay)
            status.next_retry_at = None
