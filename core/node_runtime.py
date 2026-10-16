"""Node.js 运行时查找与自动安装。

侧车已打包成单个文件（sidecar/dist/sidecar.mjs），设备上只需要一个能运行的
``node``。查找顺序：

1. WebUI 里手动填写的 node 路径
2. 系统 PATH 里的 node（版本需 >= 22）
3. 插件目录下 nodeenv 装好的 node（``.nodeenv/``）
4. 用 nodeenv 自动下载最新的 Node.js LTS 到插件目录
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import asyncio
import logging
import os
import shutil

MIN_NODE_MAJOR = 22

PLUGIN_DIR = Path(__file__).resolve().parent.parent
NODEENV_DIR = PLUGIN_DIR / ".nodeenv"


class NodeRuntimeError(RuntimeError):
    """找不到可用的 Node.js。"""


def _nodeenv_node() -> Path:
    if os.name == "nt":
        return NODEENV_DIR / "Scripts" / "node.exe"
    return NODEENV_DIR / "bin" / "node"


async def probe_node_version(node: str) -> Tuple[Optional[int], str]:
    """运行 ``node --version``，返回 (主版本号, 说明)。无法运行时主版本号为 None。"""
    try:
        process = await asyncio.create_subprocess_exec(
            node,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15.0)
    except PermissionError:
        return None, f"{node} 没有可执行权限（fnOS 等不保留权限位的磁盘上常见）"
    except (FileNotFoundError, OSError) as exc:
        return None, f"无法运行 {node}：{exc}"
    except asyncio.TimeoutError:
        return None, f"{node} --version 超时"
    version = stdout.decode("utf-8", errors="replace").strip()
    try:
        major = int(version.lstrip("v").split(".", 1)[0])
    except ValueError:
        return None, f"{node} 返回了无法识别的版本：{version!r}"
    return major, version


def _install_nodeenv() -> None:
    """在线程中执行：用 nodeenv 把最新 LTS 版 Node.js 下载到插件目录。"""
    import nodeenv

    if nodeenv.src_base_url is None:
        nodeenv.src_base_url = "https://nodejs.org/download/release"
    node_version = nodeenv.get_last_lts_node_version()
    args = nodeenv.make_parser().parse_args(["--prebuilt", "--node", node_version, "--clean-src", str(NODEENV_DIR)])
    nodeenv.create_environment(str(NODEENV_DIR), args)


async def resolve_node(configured_path: str, logger: logging.Logger) -> Tuple[str, str]:
    """按顺序查找可用的 node，返回 (路径, 版本)。都不可用时抛出 NodeRuntimeError。"""
    problems: list[str] = []

    candidates: list[Tuple[str, str]] = []
    if configured_path.strip():
        candidates.append(("WebUI 指定", configured_path.strip()))
    system_node = shutil.which("node")
    if system_node:
        candidates.append(("系统", system_node))
    if _nodeenv_node().exists():
        candidates.append(("插件目录", str(_nodeenv_node())))

    for source, path in candidates:
        major, detail = await probe_node_version(path)
        if major is None:
            problems.append(f"{source} node：{detail}")
            continue
        if major < MIN_NODE_MAJOR:
            problems.append(f"{source} node 版本 {detail} 太旧，需要 v{MIN_NODE_MAJOR} 以上")
            continue
        return path, detail

    if not _nodeenv_node().exists():
        logger.warning("=" * 60)
        logger.warning("未找到可用的 Node.js，正在自动下载最新 LTS 版到 %s", NODEENV_DIR)
        logger.warning("需要联网，约 1–2 分钟，请耐心等待…")
        logger.warning("=" * 60)
        try:
            await asyncio.to_thread(_install_nodeenv)
        except Exception as exc:  # 网络错误、磁盘错误等
            problems.append(f"自动安装 Node.js 失败：{exc}")
        else:
            major, detail = await probe_node_version(str(_nodeenv_node()))
            if major is not None and major >= MIN_NODE_MAJOR:
                logger.info("Node.js 安装完成：%s", detail)
                return str(_nodeenv_node()), detail
            problems.append(f"新装的 node 无法使用：{detail}")

    raise NodeRuntimeError("；".join(problems) or "找不到 Node.js")
