/**
 * stdout 专用于协议帧。
 *
 * 这个模块必须最先被导入：它先保存原始的 stdout 写入函数，再把 console 和
 * process.stdout 的其他写入全部转到 stderr。这样 SDK 或第三方库的任何日志都
 * 不会混进协议通道，Python 端也就不会把日志误当成协议消息。
 *
 * SDK 的日志（@photon-ai/otel，经 console.* 输出）会被整理成一行一条，去掉大段堆栈；
 * 其中「消息流中断 / 恢复」两种日志会转成 link 事件，让插件知道与 Photon 的连接状态。
 */
import { streamLabelOf, summarizeArgs, trackLinkState, trackUnsupported } from "./sdklog.ts";

const rawStdoutWrite = process.stdout.write.bind(process.stdout);
const stderrWrite = process.stderr.write.bind(process.stderr);

/** 写出一行协议帧（JSON + 换行）。 */
export function writeFrame(frame: object): void {
  rawStdoutWrite(`${JSON.stringify(frame)}\n`);
}

export type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR";

const secrets: string[] = [];

/** 登记需要在日志中遮蔽的字符串（例如 Project Secret）。 */
export function registerSecret(value: string): void {
  if (value.length >= 4) secrets.push(value);
}

/** 遮蔽已登记的密钥。 */
export function redact(text: string): string {
  let result = text;
  for (const secret of secrets) result = result.split(secret).join("***");
  return result;
}

/** 写一行日志到 stderr，格式为 `LEVEL 内容`，Python 端按第一个词解析级别。 */
export function log(level: LogLevel, message: string): void {
  const singleLine = redact(message).replace(/\r?\n/g, " ⏎ ");
  stderrWrite(`${level} ${singleLine}\n`);
}

const recentLines = new Map<string, number>();
const REPEAT_WINDOW_MS = 60_000;

/** 同一行日志 60 秒内只输出一次（断网期间底层库会反复打同一条）。 */
export function logThrottled(level: LogLevel, message: string): void {
  const now = Date.now();
  const last = recentLines.get(message);
  if (last !== undefined && now - last < REPEAT_WINDOW_MS) return;
  recentLines.set(message, now);
  if (recentLines.size > 200) {
    for (const [key, time] of recentLines) if (now - time >= REPEAT_WINDOW_MS) recentLines.delete(key);
  }
  log(level, message);
}

// ---------------------------------------------------------------------------
// SDK 日志整理与连接状态（纯函数在 sdklog.ts）
// ---------------------------------------------------------------------------

/** 把连接状态变化告诉插件。 */
export function emitLink(data: object): void {
  writeFrame({ imsg: 1, t: "evt", event: "link", data });
}

function sdkConsole(level: LogLevel) {
  return (...args: unknown[]): void => {
    trackUnsupported(args);
    const kind = trackLinkState(args, emitLink);
    // 断网期间 SDK 每次重试都会打一条，只记第一条；恢复时记一条
    if (kind === "repeat") return;
    if (kind === "recovered") {
      log("INFO", `Photon 消息流已恢复（${streamLabelOf(args)}）`);
      return;
    }
    // INFO 及以下的 SDK 日志只用来判断状态，不输出，避免刷屏
    if (level === "DEBUG" || level === "INFO") return;
    const line = summarizeArgs(args);
    if (line) logThrottled(level, line);
  };
}

// 其余所有写入 stdout 的内容改写到 stderr
process.stdout.write = ((chunk: string | Uint8Array, ...rest: unknown[]) =>
  (stderrWrite as (...args: unknown[]) => boolean)(chunk, ...rest)) as typeof process.stdout.write;
console.log = sdkConsole("INFO");
console.info = sdkConsole("INFO");
console.debug = sdkConsole("DEBUG");
console.warn = sdkConsole("WARN");
console.error = sdkConsole("ERROR");
