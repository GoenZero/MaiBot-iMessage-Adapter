/**
 * stdout 专用于协议帧。
 *
 * 这个模块必须最先被导入：它先保存原始的 stdout 写入函数，再把 console 和
 * process.stdout 的其他写入全部转到 stderr。这样 SDK 或第三方库的任何日志都
 * 不会混进协议通道，Python 端也就不会把日志误当成协议消息。
 */

const rawStdoutWrite = process.stdout.write.bind(process.stdout);
const stderrWrite = process.stderr.write.bind(process.stderr);

// 其余所有写入 stdout 的内容改写到 stderr
process.stdout.write = ((chunk: string | Uint8Array, ...rest: unknown[]) =>
  (stderrWrite as (...args: unknown[]) => boolean)(chunk, ...rest)) as typeof process.stdout.write;
console.log = console.error;
console.info = console.error;
console.debug = console.error;

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
