/**
 * Python ⇄ 侧车协议（标准输入输出，一行一个 JSON）。
 *
 * - Python → 侧车（stdin）：请求 `{"id": 1, "op": "send", "args": {...}}`
 * - 侧车 → Python（stdout）：每帧都带 `"imsg": 1` 标记
 *   - 回执 `{"imsg":1,"t":"res","id":1,"ok":true,"result":{...}}`
 *   - 失败 `{"imsg":1,"t":"res","id":1,"ok":false,"error":{"code":"...","message":"..."}}`
 *   - 事件 `{"imsg":1,"t":"evt","event":"message","data":{...}}`
 *
 * 附件不走管道：双方通过 MEDIA_DIR 里的临时文件交换，管道里只传路径。
 */

export const PROTOCOL_VERSION = 1;

export interface Request {
  id: number;
  op: string;
  args: Record<string, unknown>;
}

export interface ErrorInfo {
  code: string;
  message: string;
}

/** 带错误码的错误，用于向 Python 报告可预期的失败。 */
export class BridgeError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "BridgeError";
    this.code = code;
  }
}

/** 解析一行请求；格式不对时抛出 BridgeError。 */
export function parseRequest(line: string): Request {
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    throw new BridgeError("BAD_REQUEST", "请求不是合法 JSON");
  }
  if (typeof raw !== "object" || raw === null) throw new BridgeError("BAD_REQUEST", "请求必须是对象");
  const record = raw as Record<string, unknown>;
  const { id, op, args } = record;
  if (typeof id !== "number" || !Number.isSafeInteger(id)) throw new BridgeError("BAD_REQUEST", "缺少请求编号 id");
  if (typeof op !== "string" || !op) throw new BridgeError("BAD_REQUEST", "缺少操作名 op");
  if (args !== undefined && (typeof args !== "object" || args === null || Array.isArray(args))) {
    throw new BridgeError("BAD_REQUEST", "args 必须是对象");
  }
  return { id, op, args: (args as Record<string, unknown> | undefined) ?? {} };
}

/** 从请求参数里取必填字符串。 */
export function requireString(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value.trim()) throw new BridgeError("BAD_REQUEST", `缺少参数 ${key}`);
  return value.trim();
}

/** 从请求参数里取可选字符串。 */
export function optionalString(args: Record<string, unknown>, key: string): string | undefined {
  const value = args[key];
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") throw new BridgeError("BAD_REQUEST", `参数 ${key} 必须是字符串`);
  return value.trim() || undefined;
}

const MAX_ERROR_MESSAGE = 500;

/**
 * 把任意异常归类成错误码。
 *
 * SDK 的错误类没有全部对外导出，这里按错误名（name）判断，避免依赖内部路径。
 */
export function classifyError(error: unknown): ErrorInfo {
  if (error instanceof BridgeError) return { code: error.code, message: error.message };
  const err = error as { name?: unknown; message?: unknown; status?: unknown } | null;
  const name = typeof err?.name === "string" ? err.name : "";
  const message = typeof err?.message === "string" ? err.message : String(error);
  const short = message.length > MAX_ERROR_MESSAGE ? `${message.slice(0, MAX_ERROR_MESSAGE)}…` : message;
  if (/Target not allowed/i.test(message)) return { code: "TARGET_NOT_ALLOWED", message: short };
  // Node 的 fetch 在断网、DNS 失败时只抛 "fetch failed"，真正原因在 cause 里
  if (name === "TypeError" && /fetch failed/i.test(message)) {
    const cause = (error as { cause?: { code?: unknown; message?: unknown } }).cause;
    const detail = typeof cause?.code === "string" ? cause.code : typeof cause?.message === "string" ? cause.message : "";
    return { code: "NETWORK", message: detail ? `${short}（${detail}）` : short };
  }
  switch (name) {
    case "AuthenticationError":
      return { code: "AUTH", message: short };
    case "RateLimitError":
      return { code: "RATE_LIMIT", message: short };
    case "NotFoundError":
      return { code: "NOT_FOUND", message: short };
    case "ValidationError":
      return { code: "INVALID", message: short };
    case "UnsupportedError":
      return { code: "UNSUPPORTED", message: short };
    case "ConnectionError":
      return { code: "CONNECTION", message: short };
    case "SpectrumCloudError": {
      const status = typeof err?.status === "number" ? err.status : 0;
      return { code: status === 401 || status === 403 ? "AUTH" : `CLOUD_${status}`, message: short };
    }
    default:
      return { code: "INTERNAL", message: short };
  }
}
