# MaiBot-iMessage-Adapter

**MaiBot 的 iMessage 平台适配器插件**

通过 Photon Spectrum 云端将 [MaiBot](https://github.com/Mai-with-u/MaiBot) 接入 Apple iMessage，实现消息双向收发。

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![maibot-plugin-sdk](https://img.shields.io/badge/SDK-maibot--plugin--sdk%202.10%2B-orange)](https://github.com/Mai-with-u/maibot-plugin-sdk)

原作者：Galeros（[mayan613](https://github.com/mayan613/MaiBot-iMessage-Adapter)），原版使用教程见 [galeros.xyz](https://www.galeros.xyz/2026/07/07/imessage-adapter/)。
本仓库（GoenZero 的 Fork）从 0.2.0 起由 AI 维护并重写了传话层和侧车，改动见 [CHANGELOG.md](CHANGELOG.md)。

### 安装

将本仓库放到 MaiBot 的 `plugins/` 目录下即可。

```
cd /path/to/MaiBot/plugins
git clone https://github.com/GoenZero/MaiBot-iMessage-Adapter.git
```

### 前置条件

| 依赖 | 版本 | 用途 |
|------|------|------|
| MaiBot | 插件 SDK ≥ 2.10.0（MaiBot 1.3.5 起） | 插件宿主 |
| Node.js | ≥ 22 | 运行侧车。插件依次查找：WebUI 填写的路径 → 系统 PATH → 插件目录下的 `.nodeenv/` → 自动下载最新 LTS 版 |
| nodeenv (PyPI) | ≥ 1.10.0 | 没有 Node.js 时自动下载，MaiBot 会自动安装这个依赖 |
| pillow-heif (PyPI) | ≥ 1.8.0 | 把 iPhone 的 HEIC 照片转成 JPEG（有树莓派 ARM64 预编译包），MaiBot 会自动安装 |
| Photon 账号 | — | 在 [app.photon.codes](https://app.photon.codes) 注册 |

侧车已经在开发时打包成单个文件 `sidecar/dist/sidecar.mjs` 随仓库发布，**设备上不需要 npm install，也不需要编译**。首次启用时，如果系统里没有 Node.js，插件会自动下载（约 1–2 分钟），之后直接启动。

> **插件目录所在磁盘不保留可执行权限时（例如 fnOS 数据盘）**：自动下载的 node 可能无法运行。可以把 node 放到能执行的位置，并在 WebUI「本地侧车 → Node.js 路径」里填写完整路径。

## 架构

```
MaiBot ⇄ plugin.py ──(标准输入输出，一行一个 JSON)── Node.js 侧车 ──spectrum-ts── Photon 云端 ⇄ iMessage
                           附件经本机临时文件传递
```

- **Python 插件**（`plugin.py`、`core/`）：管理侧车进程（崩溃自动重启）、把消息在 iMessage 和 MaiBot 格式之间转换、WebUI 配置、管理命令
- **Node.js 侧车**（`sidecar/`）：用 `@spectrum-ts/core` 和 `@spectrum-ts/imessage` 12.10.1 连接 Photon；源码在 `sidecar/src/`，打包结果在 `sidecar/dist/`
- 每次发送都等侧车真正发出后，再把 iMessage 消息编号交还 MaiBot；失败会如实报告，不会「假装发出去了」

## 配置

插件配置可通过 MaiBot WebUI 的插件配置页面修改。

| 配置节 | 字段 | 默认值 | 说明 |
|--------|------|--------|------|
| `[plugin]` | `enabled` | false | 是否启用适配器 |
| `[photon]` | `project_id` | "" | Photon 项目 ID |
| `[photon]` | `project_secret` | "" | Photon 项目密钥 |
| `[bridge]` | `max_retries` | 3 | 侧车异常退出后快速重试的次数；之后间隔逐渐拉长（最长 5 分钟）继续重试，只有认证失败才停 |
| `[bridge]` | `retry_interval` | 3.0 | 重启间隔（秒） |
| `[bridge]` | `max_attachment_size_mb` | 50 | 单个附件上限（MB），收发都适用 |
| `[bridge]` | `handle_cache_size` | 500 | 记住最近多少条消息，供引用、点赞使用 |
| `[bridge]` | `node_path` | "" | Node.js 路径，留空自动查找 |
| `[capabilities]` | `typing_indicator` | true | AI 开始写回复时显示「正在输入」 |
| `[capabilities]` | `read_receipts` | true | 开始处理对方消息时自动发「已读」 |
| `[capabilities]` | `reactions` | true | 允许 AI 点爱心、点赞等（Tapback） |
| `[capabilities]` | `reply_mode` | auto | 引用回复：auto = AI 自己决定，always = 总是引用对方最新一句，off = 不引用 |
| `[capabilities]` | `markdown` | true | AI 回复里的 Markdown 格式显示成 iMessage 样式 |
| `[media]` | `image_max_edge` | 2048 | 收到的图片长边超过多少像素就缩小，0 表示不缩小；HEIC 一律转成 JPEG |

从 0.1.x 升级时，MaiBot 会自动把旧配置迁移到新结构，已填写的项目 ID、密钥等会保留；旧的「桥接端口」不再需要。

### 使用（MaiBot 配置）

MaiBot Core 用主配置里的 bot 平台账号识别「机器人自己」。启用插件后，必须在 MaiBot 配置文件夹下 `bot_config.toml` 的 `[bot]` 部分，给 `platforms` 加入：

```toml
[bot]
platforms = ["imessage:+10000000000"] # "imessage:" 后面是 Photon 给你的项目分配的号码
```

也可以在 WebUI 里设置：`麦麦设置 - 基础 - 平台账号右边的加号 - 平台 imessage，账号填 +10000000000`。不设置将无法发送消息。

初次使用必须先通过 iMessage 向 Photon 分配的号码发一条消息，否则会出现 `Target not allowed for this project`。仍报错时，到 Photon 控制台 Users 里添加号码；必要时用 debug.photon.codes 查出 Apple 实际使用的号码或邮箱再添加。

### 管理命令

| 命令 | 说明 |
|------|------|
| `/imessage_status` | 查看连接状态、版本、重启次数、最近错误 |
| `/imessage_reconnect` | 手动重连（认证失败停下后，改好配置也可以用它重试） |

### 收到的消息怎么交给 AI

| 对方发来的 | AI 看到的 |
|---|---|
| 文字 | 原文 |
| 照片（含 iPhone 默认的 HEIC） | 图片 |
| 文字和多张照片一起发 | 文字和每一张图片，顺序不变 |
| 长按某句话「回复」 | 消息本身，并标明引用的是哪一句 |
| 联系人名片 | 姓名、电话、邮箱、单位 |
| 语音、文件、链接、投票、应用卡片 | 一句说明，例如「对方发来一条语音消息，你暂时听不了语音」 |
| 其他不认识的内容 | 「对方发了一条暂不支持的内容：…」 |

任何消息都不会被整条丢掉。

## 开发

```sh
# 侧车：类型检查、单元测试、检查打包文件与源码一致
cd sidecar && npm ci && npm run check
# 修改侧车源码后重新打包，并把 dist/ 一起提交
npm run build

# Python：需要 maibot-plugin-sdk、pytest、pillow、pillow-heif、nodeenv
python -m pytest tests
```

## 许可证

本项目基于 GPL-3.0 许可证开源。`sidecar/dist/sidecar.mjs` 中包含的第三方开源软件及其许可证见 `sidecar/dist/THIRD_PARTY_LICENSES.txt`。
