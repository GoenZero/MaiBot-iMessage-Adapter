/**
 * MaiBot iMessage 适配器 · 侧车入口
 *
 * 由 Python 插件以子进程方式启动（`node dist/sidecar.mjs`），通过标准输入输出
 * 通信（协议见 protocol.ts）。职责：
 * 1. 用 spectrum-ts 连接 Photon 云端
 * 2. 把收到的 iMessage 消息转换后交给 Python（convert.ts）
 * 3. 执行 Python 发来的动作并回报结果（actions.ts）
 *
 * 退出码：0 正常退出；1 运行中出错（Python 会自动重启）；2 Photon 认证失败（不重启）。
 */
// stdio 必须最先导入：它负责把所有杂项输出从协议通道里挪走
import { emitLink, log, logThrottled, registerSecret, writeFrame } from "./stdio.ts";
import { markLinkHealthy } from "./sdklog.ts";

import { Spectrum } from "@spectrum-ts/core";
import { imessage } from "@spectrum-ts/imessage";
import { createInterface } from "node:readline";

import { Actions, type ActionMessage, type SpaceLike, sdkBuilders } from "./actions.ts";
import {
  collectMessages,
  type ContentLike,
  type ConvertOptions,
  convertInbound,
  type InboundResult,
  type MessageLike,
} from "./convert.ts";
import { LruCache } from "./handles.ts";
import { safeExtension, saveToTempFile, type StreamSource } from "./media.ts";
import { BridgeError, classifyError, parseRequest, PROTOCOL_VERSION, type Request } from "./protocol.ts";

declare const __SPECTRUM_VERSION__: string;
const SPECTRUM_VERSION = typeof __SPECTRUM_VERSION__ === "string" ? __SPECTRUM_VERSION__ : "dev";

function envString(name: string): string {
  const value = (process.env[name] ?? "").trim();
  if (!value) {
    log("ERROR", `缺少环境变量 ${name}`);
    process.exit(1);
  }
  return value;
}

function envInt(name: string, fallback: number): number {
  const value = Number.parseInt(process.env[name] ?? "", 10);
  return Number.isSafeInteger(value) && value > 0 ? value : fallback;
}

const projectId = envString("PHOTON_PROJECT_ID");
const projectSecret = envString("PHOTON_PROJECT_SECRET");
registerSecret(projectSecret);
const mediaDir = envString("MEDIA_DIR");
const maxAttachmentBytes = envInt("MAX_ATTACHMENT_BYTES", 50 * 1024 * 1024);
const handleCacheSize = envInt("HANDLE_CACHE_SIZE", 500);

// gRPC 底层偶尔会在连接被重置时抛出没人接住的 socket 错误（例如经 HTTP 代理时）。
// 这类错误由 SDK 的重连逻辑兜底，记一条警告即可；其他未知错误仍按崩溃处理，交给 Python 重启。
process.on("uncaughtException", (error: NodeJS.ErrnoException) => {
  if (error?.code === "ECONNRESET" || error?.code === "EPIPE" || error?.code === "ETIMEDOUT") {
    logThrottled("WARN", `网络连接被重置（${error.code}），SDK 会自动重连`);
    return;
  }
  log("ERROR", `侧车未捕获的错误：${String(error?.stack ?? error)}`);
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  log("ERROR", `未处理的异步错误：${String((reason as Error)?.stack ?? reason)}`);
});

function emit(event: string, data: object): void {
  writeFrame({ imsg: PROTOCOL_VERSION, t: "evt", event, data });
}

// ---------------------------------------------------------------------------
// 1. 连接 Photon
// ---------------------------------------------------------------------------

let app: Awaited<ReturnType<typeof Spectrum>>;
try {
  app = await Spectrum({
    projectId,
    projectSecret,
    providers: [imessage.config()],
    // info 级日志只用于判断「消息流中断 / 恢复」，stdio.ts 不会把它们输出
    options: { logLevel: "info" },
  });
} catch (error) {
  const info = classifyError(error);
  log("ERROR", `连接 Photon 失败 [${info.code}]：${info.message}`);
  emit("fatal", info);
  process.exit(info.code === "AUTH" ? 2 : 1);
}

const provider = imessage(app);
const spaces = new Map<string, SpaceLike>();
const handles = new LruCache<ActionMessage>(handleCacheSize);
const seen = new LruCache<true>(2000);

/** 裸号码或邮箱补成私聊对话编号；已知对话直接复用。 */
async function resolveSpace(chatId: string): Promise<SpaceLike> {
  const id = chatId.includes(";") ? chatId : `any;-;${chatId}`;
  const known = spaces.get(id);
  if (known) return known;
  const space = (await provider.space.get(id)) as unknown as SpaceLike;
  spaces.set(space.id, space);
  return space;
}

const actions = new Actions({
  resolveSpace,
  handles,
  mediaDir,
  maxAttachmentBytes,
  builders: sdkBuilders,
  warn: (message) => log("WARN", message),
});

// ---------------------------------------------------------------------------
// 2. 处理 Python 发来的请求
// ---------------------------------------------------------------------------

let stopping = false;

async function shutdown(code: number): Promise<never> {
  if (stopping) {
    // 已经在关闭（例如 Python 要求退出时，消息流随之结束）：交给第一次调用去退出
    return new Promise<never>(() => undefined);
  }
  stopping = true;
  log("INFO", "侧车正在关闭");
  await Promise.race([app.stop().catch(() => undefined), new Promise((resolve) => setTimeout(resolve, 3000))]);
  process.exit(code);
}

async function dispatch(request: Request): Promise<object> {
  switch (request.op) {
    case "ping":
      return { protocol: PROTOCOL_VERSION, spectrum: SPECTRUM_VERSION, node: process.version, handles: handles.size };
    case "send":
      return actions.send(request.args);
    case "react":
      return actions.react(request.args);
    case "edit":
      return actions.edit(request.args);
    case "unsend":
      return actions.unsend(request.args);
    case "background":
      return actions.background(request.args);
    case "share_contact":
      return actions.shareContact(request.args);
    case "place_sticker":
      return actions.placeSticker(request.args);
    case "typing":
      return actions.typing(request.args);
    case "mark_read":
      return actions.markRead(request.args);
    case "shutdown":
      setImmediate(() => void shutdown(0));
      return {};
    default:
      throw new BridgeError("UNKNOWN_OP", `不认识的操作：${request.op}`);
  }
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  if (!line.trim()) return;
  let request: Request;
  try {
    request = parseRequest(line);
  } catch (error) {
    log("WARN", `收到无法解析的请求：${classifyError(error).message}`);
    return;
  }
  // 请求并发处理；同一会话的发送顺序由 Python 端逐条等待回执来保证
  dispatch(request).then(
    (result) => writeFrame({ imsg: PROTOCOL_VERSION, t: "res", id: request.id, ok: true, result }),
    (error: unknown) => {
      const info = classifyError(error);
      log("WARN", `操作 ${request.op} 失败 [${info.code}]：${info.message}`);
      writeFrame({ imsg: PROTOCOL_VERSION, t: "res", id: request.id, ok: false, error: info });
    },
  );
});
// Python 端关闭管道（插件卸载或崩溃）时随之退出
input.on("close", () => void shutdown(0));
process.on("SIGTERM", () => void shutdown(0));
process.on("SIGINT", () => void shutdown(0));

emit("ready", { protocol: PROTOCOL_VERSION, spectrum: SPECTRUM_VERSION, node: process.version });
log("INFO", `已连接 Photon（spectrum-ts ${SPECTRUM_VERSION}，Node ${process.version}）`);

// ---------------------------------------------------------------------------
// 3. 接收 iMessage 消息
// ---------------------------------------------------------------------------

const MAX_TEXT_FILE_BYTES = envInt("MAX_TEXT_FILE_BYTES", 256 * 1024);

/** 下载附件并记录耗时（用于排查大文件接收慢的问题）。 */
async function downloadWithTiming(content: ContentLike, limit: number): Promise<{ path: string; size: number }> {
  const started = Date.now();
  const saved = await saveToTempFile(
    content as unknown as StreamSource,
    mediaDir,
    safeExtension(String(content.name ?? ""), String(content.mimeType ?? "")),
    limit,
  );
  const seconds = (Date.now() - started) / 1000;
  log(
    seconds >= 10 ? "WARN" : "INFO",
    `附件下载完成：${String(content.name ?? "")}（${(saved.size / 1024 / 1024).toFixed(1)}MB），用时 ${seconds.toFixed(1)} 秒`,
  );
  return saved;
}

const convertOptions: ConvertOptions = {
  lookupMessage: (id: string) => handles.get(id),
  maxImageBytes: maxAttachmentBytes,
  maxTextFileBytes: MAX_TEXT_FILE_BYTES,
  warn: (message: string) => log("WARN", message),
  saveImage: (content: ContentLike) => downloadWithTiming(content, maxAttachmentBytes),
};
// 读取文本文件可在 WebUI 关闭（READ_TEXT_FILES=0）
if (process.env.READ_TEXT_FILES !== "0") {
  convertOptions.saveTextFile = (content: ContentLike) => downloadWithTiming(content, MAX_TEXT_FILE_BYTES);
}
// 开启「语音转文字」（TRANSCRIBE_VOICE=1）时才下载语音
if (process.env.TRANSCRIBE_VOICE === "1") {
  convertOptions.saveVoice = (content: ContentLike) => downloadWithTiming(content, maxAttachmentBytes);
}

try {
  for await (const [space, rawMessage] of app.messages) {
    // 收到消息说明与 Photon 的连接是通的（SDK 的「已恢复」日志可能晚到或不出现）
    markLinkHealthy(emitLink);
    spaces.set(space.id, space as unknown as SpaceLike);
    const message = rawMessage as unknown as MessageLike;
    for (const item of collectMessages(message)) handles.set(item.id, item as ActionMessage);
    // 断线重连时 SDK 可能重放事件，按编号去重
    if (seen.has(message.id)) continue;
    seen.set(message.id, true);

    let result: InboundResult;
    try {
      result = await convertInbound(message, convertOptions);
    } catch (error) {
      // 转换本身出错也不能丢消息：退化成一条占位
      log("ERROR", `转换入站消息失败（${message.id}）：${String((error as Error)?.stack ?? error)}`);
      result = {
        kind: "message",
        data: {
          message_id: message.id,
          chat_id: space.id,
          chat_type: "dm",
          sender_id: message.sender?.id ?? "",
          timestamp: Date.now(),
          parts: [{ type: "unsupported", what: "无法解析的消息" }],
        },
      };
    }
    if (result.kind === "message") {
      const delay = (Date.now() - result.data.timestamp) / 1000;
      if (delay >= 30) log("WARN", `消息 ${message.id} 在发出 ${delay.toFixed(0)} 秒后才交给适配器（含附件下载时间）`);
      emit("message", result.data);
    }
    else if (result.kind === "control") emit("control", result.data);
  }
  if (!stopping) log("ERROR", "Photon 消息流意外结束");
} catch (error) {
  if (!stopping) {
    const info = classifyError(error);
    log("ERROR", `Photon 消息流出错 [${info.code}]：${info.message}`);
    emit("fatal", info);
  }
}
await shutdown(1);
