# 代码审查与压力测试说明（给外部审查 AI）

本文写给受托审查本仓库的 AI（例如 GPT / Codex）。使用者不写代码，请直接按本文执行，最后用**中文**交一份报告。

仓库：`GoenZero/MaiBot-iMessage-Adapter`，分支 `feature/photon-capabilities`。

---

## 一、你的任务

1. **代码审查**：找出真实存在的 bug（结果错误、崩溃、卡死、资源泄漏、安全问题），不要报代码风格问题。
2. **压力和异常测试**：在本地用假侧车、假数据模拟高负载和各种异常，看插件会不会崩溃、卡住、丢消息、内存只涨不降。
3. **交一份报告**，格式见第七节。

**只出报告，不改代码。** 不要提交，也不要推送到任何分支。为了复现问题写的测试脚本，放在报告附录里即可。修复由仓库的维护方（另一个 AI 会话）统一负责，这样两边不会改出冲突。

## 二、这个项目是什么

MaiBot（一个 Python 写的聊天机器人框架，版本 1.3.5）的 iMessage 适配器插件。插件通过 Photon 公司的云服务收发 iMessage。

```
MaiBot ⇄ plugin.py（Python 插件）──标准输入输出，一行一个 JSON──  Node.js 侧车  ──spectrum-ts SDK──  Photon 云端 ⇄ iPhone
                                    附件通过本机临时文件交换
```

| 位置 | 内容 |
|---|---|
| `plugin.py` | 插件主体：生命周期、侧车管理、收消息、发消息（网关）、钩子、管理命令 |
| `tools.py` | 给 AI 用的工具：点赞、编辑、撤回、特效、投票、聊天背景、分享名片、贴贴纸 |
| `config.py` | WebUI 配置项 |
| `core/bridge.py` | 侧车进程管理：启动、请求与回执、超时、崩溃后指数退避重启 |
| `core/inbound.py` | 侧车送来的消息 → MaiBot 消息格式 |
| `core/outbound.py` | MaiBot 要发的消息 → 侧车发送指令 |
| `core/media.py` | 图片处理（HEIC 转换、缩小、贴纸） |
| `core/capabilities.py` | 能力注册表与给 AI 的能力说明 |
| `core/planner.py`、`core/sessions.py`、`core/events.py`、`core/typing.py`、`core/effects.py`、`core/lru.py` | 规划器钩子、会话映射、事件通知文字、输入中提示、特效名、缓存 |
| `sidecar/src/*.ts` | Node 侧车源码（TypeScript） |
| `sidecar/build-config.mjs` | 打包配置，包括对 `@spectrum-ts/imessage` 的**打包时补丁** |
| `sidecar/dist/sidecar.mjs` | 打包好的侧车（随仓库提交，设备上不编译） |
| `tests/` | Python 测试；`tests/fake_sidecar.mjs` 是不连 Photon 的假侧车 |
| `docs/` | 审计报告、三次交付说明（`DELIVERY-1/2/3.md`）、本文 |

## 三、运行环境（审查时要考虑）

- **设备**：树莓派 4B，4GB 内存，ARM64，MaiBot 跑在 Docker 里，长期运行。
- **Photon**：免费版，**共享号码**，只和一个人一对一私聊，不做群聊。
- **Node.js**：22 或以上；Python 3.13。

## 四、怎么跑测试

```sh
# Python（建议用独立的虚拟环境）
pip install maibot-plugin-sdk==2.10.0 pytest pillow pillow-heif nodeenv
python -m pytest tests            # 当前应全部通过（77 项）

# 侧车
cd sidecar
npm ci
npm run check                     # 类型检查 + 单元测试 + 打包文件与源码一致（当前 46 项全部通过）
```

测试不连网络，不需要任何密钥。

## 五、重点审查和测试的方向

1. **绝不整条丢消息**：任何一种对方发来的内容（文字、图片、HEIC、贴纸、语音、文件、名片、投票、组合消息、回复、特效……）都必须变成 MaiBot 能看到的东西。下载失败、格式不认识时，也要有占位文字。
2. **不崩溃、不卡死**：侧车崩溃、回复超时、输出乱码或半行 JSON、Photon 断网时，插件要能恢复，聊天不能卡住。重点看 `core/bridge.py` 和 `sidecar/src/main.ts`。
3. **长期运行的资源**：缓存（`LruDict`、侧车的 `LruCache`、各种按聊天记录的字典）是否有上限；临时文件（`MEDIA_DIR`）是否都会删掉；后台任务是否会越积越多。
4. **并发与顺序**：同时进来很多条消息、同时调用多个工具、发消息过程中侧车重启。
5. **安全**：
   - Photon 项目密钥不能出现在日志里（看 `_redact` 和侧车的 `registerSecret`）。
   - 侧车只能读写 `MEDIA_DIR` 里的文件（看 `readOutboundFile`）。
   - 对方发来的内容（文件名、文字）不能导致路径穿越或命令执行。
6. **配置热更新**：在 WebUI 改配置后，该重连的重连，不该重连的不重连（`on_config_update`、`_connection_settings`）。
7. **在树莓派上的性能**：大图片、长文本文件、大量消息时的 CPU 和内存。

## 六、请不要误报的已知设计

下面这些是有意这样做的，不是 bug（除非你发现它们本身有缺陷）：

- `sidecar/dist/sidecar.mjs` 提交到仓库：设备上不编译，避免在树莓派上执行 `npm install`。
- `sidecar/build-config.mjs` 在打包时修改 `@spectrum-ts/imessage` 的源码（投票标题、转发编辑/撤回/贴纸/投票事件、贴贴纸入口等）：SDK 不支持这些功能，每处修补都有测试，找不到要改的代码时打包直接失败。
- 侧车崩溃后会一直重试，不会放弃（间隔逐渐拉长）：只有 Photon 认证失败才停。
- 写进对方消息里的方括号标签只写中性描述，不写给 AI 的指令：这些文字会被 MaiBot 的记忆模块当成对方的话存下来。
- 能力说明（`core/capabilities.py` 的 `build_brief`）控制在 260 字以内。
- 对方的点赞、已读等事件写进 AI 的上下文，但不会叫醒 AI。
- 只支持一对一私聊，没做群聊。
- 语音合成（发语音）只做了预留，`voice_send` 永远视为未配置。

## 七、绝对不要做的事

- **不要连接真实的 Photon 服务**，不要找或使用任何真实的 Project ID、Project Secret、手机号码。压力测试一律用 `tests/fake_sidecar.mjs`，或者自己写的假对象。
- 不要修改、提交或推送代码（见第一节）。
- 不要升级依赖版本。

## 八、报告格式（中文）

```
# 审查报告

## 运行了哪些测试
（命令、结果；自己写的压力测试做了什么、跑了多久、数据多大）

## 确认的问题（已经复现）
### 1. 一句话标题
- 严重程度：高 / 中 / 低
- 位置：文件:行号
- 触发场景：在什么情况下出现（越具体越好）
- 后果：用户会看到什么（例如「这条消息 AI 收不到」「插件卡住要重启」）
- 证据：复现步骤或测试脚本（放附录）
- 建议的修法

## 疑似问题（没能复现，或者需要真机确认）
（同上格式，说明为什么没能确认）

## 检查过、确认没问题的地方
（列出看过的高风险点，避免下次重复检查）

## 附录：复现用的测试脚本
```

请按严重程度从高到低排列。没把握的内容放进「疑似问题」，不要写成确认的问题。
