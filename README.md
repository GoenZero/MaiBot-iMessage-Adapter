# MaiBot-iMessage-Adapter

**MaiBot 的 iMessage 平台适配器插件**

通过 Photon Spectrum 云端将 [MaiBot](https://github.com/Mai-with-u/MaiBot) 接入 Apple iMessage，实现消息双向收发。

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![maibot-plugin-sdk](https://img.shields.io/badge/SDK-maibot--plugin--sdk%202.10%2B-orange)](https://github.com/Mai-with-u/maibot-plugin-sdk)

原作者：Galeros（[mayan613](https://github.com/mayan613/MaiBot-iMessage-Adapter)），原版使用教程见 [galeros.xyz](https://www.galeros.xyz/2026/07/07/imessage-adapter/)。
本仓库（GoenZero 的 Fork）从 0.2.0 起由 AI 维护并重写了传话层和侧车，改动见 [CHANGELOG.md](CHANGELOG.md)。

## 设计初衷

**让 AI 根据真实的对话，自己选择怎么表现。**

和真人用 iMessage 聊天时，我们不只是打字：会给一句话点个爱心，会撤回打错的字，生日时放一场烟花，看到好笑的消息贴一张贴纸。对方也一样，会用特效、贴纸、点赞、编辑和撤回来表达。原版适配器只负责收发文字和图片，这些表达 AI 既看不到，也用不了。

这个版本想做的，不是给 AI 写死「遇到什么情况就做什么」的规则，而是做到下面四点：

1. **让 AI 看见真实发生了什么**：对方用了什么特效、特效套在哪几个字上、贴了什么贴纸、点了什么赞、改了或撤回了哪句话、有没有读消息，都如实告诉 AI。
2. **让 AI 知道自己能做什么**：每轮对话前，用一小段说明告诉 AI 它在 iMessage 里有哪些能力。每个工具也写清楚适合什么时候用、什么时候不该用。
3. **由 AI 自己判断要不要用、怎么用**：点不点赞、发不发特效、贴不贴贴纸，由 AI 看当时的对话决定，也可以什么都不做。
4. **做不到的就如实说**：iMessage 或 Photon 做不到的事（例如发 iOS 18 文字效果），AI 会告诉对方做不到，不会拿别的东西顶替，也不会假装已经做了。

同时守住几条底线：

- 任何消息都不整条丢掉。
- 每项能力都能在 WebUI 里单独关掉，关掉后 AI 就看不到这项能力。
- 写进对方消息里的标签只写中性描述，不会被 MaiBot 的记忆当成对方说过的话。
- 稳定优先于新功能。

## 与原版相比新增了什么

以原版 0.1.5 为基准：

| 方面 | 原版 | 本版本 |
|---|---|---|
| AI 能主动做的事 | 回复文字和图片 | 另外还能点赞回应、编辑和撤回自己的消息、发气泡和屏幕特效、发起投票、设置聊天背景、把表情包当贴纸贴在消息上、分享名字与头像（共 8 个工具） |
| AI 能看到的对方动作 | 文字和图片 | 另外还有点赞和取消点赞、投票选择、已读、编辑、撤回、气泡和屏幕特效、iOS 18 文字效果（以及套在哪几个字上）、贴纸、贴在消息上的贴纸 |
| 语音 | 拦截，AI 看不到 | 可以转成文字交给 AI；不转时 AI 会请对方打字 |
| iPhone 照片（HEIC） | 拦截 | 转成 JPEG 并摆正方向，大图自动缩小 |
| 名片、文本文件、回复引用、文字加多图 | 名片没有内容；部分组合消息会被整条丢掉 | 名片的姓名、电话等，.txt、.md 文件的内容，引用的是哪一句，都交给 AI；任何消息都不整条丢掉 |
| 发送的样子 | 纯文字 | 正在输入提示、自动已读、引用回复（AI 决定 / 总是 / 关闭）、Markdown 样式、链接预览卡片 |
| AI 知不知道自己能做什么 | 不知道 | 每轮对话前有一段能力说明；每项能力都有 WebUI 开关 |
| 配置机器人号码 | 要手动改 `bot_config.toml` | 在插件里填写「Photon 号码」，自动上报给 MaiBot |
| 安装 | 设备上要 `npm install` 和编译 | 侧车已打包随仓库提交，设备上不用安装和编译 |
| 和侧车的连接 | 本机 WebSocket 端口（18763） | 标准输入输出，不占端口 |
| 附件 | 默认上限 10MB，过大会把连接撑断 | 默认上限 50MB，走临时文件 |
| 出错时 | 重启几次后放弃 | 一直自动重试（间隔逐渐拉长）；下载卡住不会挡住后面的消息；同一个对话的消息按顺序发；发送失败会如实报告 |

逐条变化见 [CHANGELOG.md](CHANGELOG.md)，每个阶段的说明见 `docs/` 目录。

### 安装

将本仓库放到 MaiBot 的 `plugins/` 目录下即可。

```
cd /path/to/MaiBot/plugins
git clone https://github.com/GoenZero/MaiBot-iMessage-Adapter.git
```

### 前置条件

| 依赖 | 版本 | 用途 |
|------|------|------|
| MaiBot | 插件 SDK ≥ 2.10.0（MaiBot 1.3.5 起，已在 1.3.9 上验证） | 插件宿主 |
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
| `[photon]` | `bot_number` | "" | Photon 分配的 iMessage 号码，填写后自动上报为机器人账号 |
| `[bridge]` | `max_retries` | 3 | 侧车异常退出后快速重试的次数；之后间隔逐渐拉长（最长 5 分钟）继续重试，只有认证失败才停 |
| `[bridge]` | `retry_interval` | 3.0 | 重启间隔（秒） |
| `[bridge]` | `max_attachment_size_mb` | 50 | 单个附件上限（MB），收发都适用 |
| `[bridge]` | `handle_cache_size` | 500 | 记住最近多少条消息，供引用、点赞使用 |
| `[bridge]` | `node_path` | "" | Node.js 路径，留空自动查找 |
| `[bridge]` | `recover_missed` | true | 漏消息自动补收：按需对一下 Photon 上对方最近发的消息，没推送过来的自动补上（刚连上或断线恢复后半小时内每 2 分钟一次，聊天中每 10 分钟，没人聊天时每 30 分钟） |
| `[capabilities]` | `typing_indicator` | true | AI 开始写回复时显示「正在输入」 |
| `[capabilities]` | `read_receipts` | true | 开始处理对方消息时自动发「已读」 |
| `[capabilities]` | `reactions` | true | 允许 AI 点爱心、点赞等（Tapback） |
| `[capabilities]` | `reply_mode` | auto | 引用回复：auto = AI 自己决定，always = 总是引用对方最新一句，off = 不引用 |
| `[capabilities]` | `markdown` | true | AI 回复里的 Markdown 格式显示成 iMessage 样式 |
| `[capabilities]` | `read_text_files` | true | 对方发来 .txt、.md 等文本文件时，把内容交给 AI |
| `[capabilities]` | `edit_unsend` | true | 允许 AI 修改、撤回自己刚发的消息，以及撤回自己点的回应 |
| `[capabilities]` | `effects` | true | 允许 AI 发带特效的消息（气球、烟花、隐形墨水等） |
| `[capabilities]` | `polls` | true | 允许 AI 发起投票 |
| `[capabilities]` | `chat_background` | true | 你要求时，AI 可以设置或清除聊天背景 |
| `[capabilities]` | `stickers` | true | 允许 AI 从 MaiBot 表情包库挑一张，当贴纸贴在你的某条消息上 |
| `[capabilities]` | `share_contact` | true | 你要求时，AI 可以分享机器人账号的名字和头像 |
| `[capabilities]` | `link_preview` | true | 一段回复只有一个链接时，以链接预览卡片发出 |
| `[capabilities]` | `notify_reactions` | true | 对方点赞、在投票里选了选项时告诉 AI |
| `[capabilities]` | `notify_edits` | true | 对方编辑、撤回自己的消息时告诉 AI，并附上原来那句 |
| `[capabilities]` | `notify_read` | true | 对方读了 AI 的消息时告诉 AI（同一批只提醒一次） |
| `[media]` | `image_max_edge` | 2048 | 收到的图片长边超过多少像素就缩小，0 表示不缩小；HEIC 一律转成 JPEG |
| `[voice]` | `transcribe` | false | 收到语音时用 MaiBot 的语音识别转成文字（要先在 MaiBot 配好语音识别模型）。关闭时 AI 会请对方改发文字 |
| `[voice]` | `tts_service` | "" | 预留：以后接入语音合成时使用，当前版本不会用到 |

从 0.1.x 升级时，MaiBot 会自动把旧配置迁移到新结构，已填写的项目 ID、密钥等会保留；旧的「桥接端口」不再需要。

### 使用（MaiBot 配置）

MaiBot 需要知道「机器人自己」在 iMessage 上是哪个号码。

> **MaiBot 1.3.6 及以后的版本必须用第一种方式**：没填「Photon 号码」时，MaiBot 会拒收所有 iMessage 消息（日志里是「入站消息缺少明确的 account_id 归属」），第二种方式不再起作用。

1. **在本插件配置页「Photon 云端 → Photon 号码」填写 Photon 分配给你的项目的号码**（带国家代码，例如 `+14155550100`）。适配器连上 Photon 时会把它作为机器人账号上报给 MaiBot，不需要再做别的设置。
   - 注意：首次填写或更改号码后，MaiBot 会把这个 iMessage 对话当作一个**新的聊天流**（MaiBot 的聊天流编号里含机器人账号），之前那条聊天流的近期上下文不会接上。
2. 手动配置备用账号（只适用于 MaiBot 1.3.5）：在 MaiBot WebUI 的**插件配置页（适配器设置）→ 右上角「平台账号」→ 展开 → 备用账号**里添加，平台填 `imessage`，账号填 Photon 号码；或者直接编辑 `bot_config.toml`：
   ```toml
   [bot]
   platforms = ["imessage:+14155550100"]
   ```
   这是 MaiBot 1.3.5 的位置（`bot.platforms`，界面上叫「其他备用平台 / 备用账号」）。适配器上报了账号之后，这里的备用账号就不再参与判断。

初次使用必须先通过 iMessage 向 Photon 分配的号码发一条消息，否则会出现 `Target not allowed for this project`。仍报错时，到 Photon 控制台 Users 里添加号码；必要时用 debug.photon.codes 查出 Apple 实际使用的号码或邮箱再添加。

### 管理命令

| 命令 | 说明 |
|------|------|
| `/imessage_status` | 查看连接状态、版本、重启次数、最近错误 |
| `/imessage_reconnect` | 手动重连（认证失败停下后，改好配置也可以用它重试） |
| `/imessage_missed` | 「消息被吞」时排查：对照 Photon 上的消息记录，看你发的消息适配器有没有收到。可以写时间，例如 `/imessage_missed 13:40 14:00`；不写就查最近 60 分钟。只显示时间和字数，不显示内容 |

### AI 能做的事

除了正常回复，AI 在 iMessage 私聊里还能用这些工具（每项都能在 WebUI「iMessage 能力」里单独关掉，关掉后 AI 就看不到）：

| 工具 | 作用 |
|---|---|
| `imessage_react` | 给某条消息点爱心、赞、哈哈等（Tapback），也能撤回自己点过的 |
| `imessage_edit` | 修改自己刚发的消息（iMessage 限 15 分钟内） |
| `imessage_unsend` | 撤回自己刚发的消息（iMessage 限 2 分钟内） |
| `imessage_send_effect` | 发一条带特效的消息，例如生日时放烟花 |
| `imessage_poll` | 发起投票 |
| `imessage_background` | 把你发的某张图片设为聊天背景，或清除背景 |
| `imessage_place_sticker` | 从 MaiBot 的表情包库挑一张，当贴纸贴在你的某条消息上 |
| `imessage_share_contact` | 用「共享名字与照片」把机器人账号的名字和头像发给你 |

另外这些是自动完成的：输入中提示、自动已读、引用回复、Markdown 样式、链接预览卡片。

### 收到的消息怎么交给 AI

| 对方发来的 | AI 看到的 |
|---|---|
| 文字 | 原文 |
| 照片（含 iPhone 默认的 HEIC） | 图片 |
| 贴纸（苹果自带、Memoji、第三方贴纸包、照片抠出来的贴纸） | 交给 MaiBot 的表情包系统识别，例如「[表情包：一只猫在比心]」 |
| 贴在某条消息上的贴纸 | 引用那条消息 + 贴纸 |
| 带气泡特效、屏幕特效、iOS 18 文字效果的消息 | 原文，后面加「[放大特效]」「[文字效果：正文里「……」这几个字带「摇晃」效果]」这样的标签 |
| 文字和多张照片一起发 | 文字和每一张图片，顺序不变 |
| 长按某句话「回复」 | 消息本身，并标明引用的是哪一句 |
| 联系人名片 | 姓名、电话、邮箱、单位 |
| .txt、.md 等文本文件 | 文件内容（最多 6000 字） |
| 语音 | 开启「语音转文字」时是「[语音消息] 转写结果」；否则是「[语音消息（约 N 秒）]」，能力说明会告诉 AI 请对方打字 |
| 其他文件、链接、投票、应用卡片 | 一句说明 |
| 给消息点赞（或取消）、在投票里选选项、编辑或撤回自己的消息、已读 | 一条系统通知，例如 `<imessage_event>对方给你的消息「晚安」点了「爱心」回应</imessage_event>`。不会叫醒 AI，AI 下一轮能看到 |
| 其他不认识的内容 | 「对方发了一条暂不支持的内容：…」 |

任何消息都不会被整条丢掉。

## 已知限制

- **只支持一对一私聊**，没有做群聊。
- **免费版 Photon**：只支持手机号，不支持用邮箱收发的 iMessage；不能主动发起对话，必须先由对方给 Photon 号码发一条消息。
- **AI 发不了 iOS 18 文字效果**（摇晃、爆发等），只能看懂对方发的。
- **AI 不能单独发一张贴纸**，只能把贴纸贴在某条消息上；单独发的表情包是普通图片。
- **AI 不能发语音**：这项能力只做了预留。
- **「共享名字与照片」的名字和头像**来自 Photon 账号的设置，插件改不了。
- **贴纸来自哪个 App 看不出来**：传过来的只是一张图片。

## 故障排查

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| `/imessage_status` 显示未运行或等待重连 | 找不到 Node.js，或 Node.js 没有执行权限 | 看 MaiBot 日志里带 `[侧车]` 的行；在 WebUI「本地侧车 → Node.js 路径」填写能运行的 node 路径 |
| 显示「Photon 认证失败」 | 项目 ID 或密钥填错 | 在 WebUI 改好后会自动重连，也可以发 `/imessage_reconnect` |
| 报 `Target not allowed for this project` | 对方还没给 Photon 号码发过消息 | 先用 iMessage 给 Photon 号码发一条消息；仍不行就到 Photon 控制台 Users 里添加号码 |
| 能收不能发 | MaiBot 不知道机器人号码 | 在插件配置里填写「Photon 号码」（见上文「使用」） |
| 显示「与 Photon 的连接中断，正在自动重连」 | 网络或 Photon 服务波动 | 一般会自己恢复；`/imessage_status` 会显示中断了多久 |
| 发的消息 AI 说没收到 | 消息没到 Photon，或 Photon 的实时推送漏了（「漏消息自动补收」开着时，几分钟内会自动补上），或 MaiBot 没接收 | 发 `/imessage_missed 开始时间 结束时间`：Photon 上没有这条，问题在 Photon 之前；有但标着「适配器没收到」，是 Photon 推送漏了；适配器收到了，就看 MaiBot 日志里同一时间有没有「MaiBot 没有接收」 |
| AI 从来不用某个能力 | 这项能力被关掉了；或者 AI 判断当时不适合用 | `/imessage_status` 能看到各项开关；特效、贴纸等工具本来就要求 AI 少用 |
| 语音只显示「[语音消息（约 N 秒）]」 | 没开「语音转文字」，或 MaiBot 没配置语音识别模型 | WebUI「语音」页打开，并在 MaiBot「模型配置」里配好语音识别 |

## 隐私与安全

- Photon 项目密钥只保存在本机的插件配置里；日志里出现时会被遮住，改过密钥后旧密钥也照样遮住。
- 消息经 Photon 的云端服务转发到 iMessage，请同时留意 Photon 的服务条款。
- 收发的附件只在本机临时目录里中转，用完就删。侧车只能读写这个临时目录。

## 开发

```sh
# 侧车：类型检查、单元测试、检查打包文件与源码一致
cd sidecar && npm ci && npm run check
# 修改侧车源码后重新打包，并把 dist/ 一起提交
npm run build

# Python：需要 maibot-plugin-sdk、pytest、pillow、pillow-heif、nodeenv
python -m pytest tests
```

## 贡献者

| 贡献者 | 做了什么 |
|---|---|
| Galeros（[mayan613](https://github.com/mayan613)） | 原作者：原版适配器（0.1.x） |
| [GoenZero](https://github.com/GoenZero) | 0.2.0 的发起与维护：提出需求和设计初衷，真机测试与验收 |
| Claude（Anthropic） | 0.2.0 的开发：重写传话层和侧车，实现各项 iMessage 能力，编写测试和文档 |
| GPT（OpenAI Codex） | 0.2.0 的外部代码审查与压力测试 |

## 致谢

- 原作者 Galeros（[mayan613/MaiBot-iMessage-Adapter](https://github.com/mayan613/MaiBot-iMessage-Adapter)）：本项目在原版的基础上改进，保留原作者署名。
- [MaiBot](https://github.com/Mai-with-u/MaiBot) 和 maibot-plugin-sdk。
- [Photon](https://app.photon.codes) 提供的 iMessage 云端服务和 spectrum-ts SDK。

## 许可证

本项目基于 GPL-3.0 许可证开源。`sidecar/dist/sidecar.mjs` 中包含的第三方开源软件及其许可证见 `sidecar/dist/THIRD_PARTY_LICENSES.txt`。
