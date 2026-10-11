/**
 * SDK 日志整理与连接状态判断（纯函数，没有副作用，可单独测试）。
 */
import { format } from "node:util";

const MAX_SDK_LOG = 400;

/** 把 console.* 的参数整理成简短的一行：对象只保留关键信息，错误只保留类型和消息。 */
export function summarizeArgs(args: unknown[]): string {
  const parts = args.map((arg) => {
    if (arg instanceof Error) {
      const cause = arg.cause instanceof Error ? `（原因：${arg.cause.message.split("\n")[0]}）` : "";
      return `${arg.name}: ${arg.message.split("\n")[0]}${cause}`;
    }
    if (typeof arg === "object" && arg !== null) {
      const record = arg as Record<string, unknown>;
      const keep = ["spectrum.stream.label", "spectrum.stream.attempt", "spectrum.error.message", "spectrum.error.cause.message"];
      const picked = keep.filter((key) => record[key] !== undefined).map((key) => `${key.split(".").pop()}=${String(record[key])}`);
      return picked.length ? `{${picked.join(", ")}}` : "";
    }
    return typeof arg === "string" ? arg : format("%o", arg);
  });
  const line = parts.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  return line.length > MAX_SDK_LOG ? `${line.slice(0, MAX_SDK_LOG)}…` : line;
}

/** 正在中断的消息流（按 label 区分；SDK 对消息、投票等各有一条流）。 */
const interruptedStreams = new Set<string>();

function streamLabel(args: unknown[]): string {
  const attrs = args.find((arg) => typeof arg === "object" && arg !== null && !(arg instanceof Error)) as
    | Record<string, unknown>
    | undefined;
  return String(attrs?.["spectrum.stream.label"] ?? "default");
}

export type StreamLogKind = "interrupted" | "repeat" | "recovered" | "other";

/**
 * 根据 SDK 日志更新连接状态；整体状态变化时发 link 事件。
 *
 * 返回这条日志的类别：同一条流已经在中断中时，后续每次重试的日志算 repeat（调用方可以不输出，
 * 避免断网期间刷屏）。
 */
export function trackLinkState(args: unknown[], emit: (data: object) => void): StreamLogKind {
  const text = args.filter((arg) => typeof arg === "string").join(" ");
  const label = streamLabel(args);
  if (/stream interrupted; reconnecting|stream persistently failing/.test(text)) {
    if (interruptedStreams.has(label)) return "repeat";
    const wasHealthy = interruptedStreams.size === 0;
    interruptedStreams.add(label);
    if (wasHealthy) emit({ state: "reconnecting", detail: summarizeArgs(args) });
    return "interrupted";
  }
  if (/stream recovered/.test(text) && interruptedStreams.delete(label)) {
    if (interruptedStreams.size === 0) emit({ state: "ok" });
    return "recovered";
  }
  return "other";
}

export function streamLabelOf(args: unknown[]): string {
  return streamLabel(args);
}
