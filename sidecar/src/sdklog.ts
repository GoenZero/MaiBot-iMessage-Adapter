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

/**
 * 正在中断的流（按 label 区分）。SDK 对每个号码开两条流：消息（imessage.messages:…）和投票
 * （imessage.polls:…）。连接状态只看消息流：投票流平时几乎没有事件，而 SDK 只在重连后收到
 * 第一个事件（或补齐了漏掉的事件）时才记「stream recovered」，投票流可能很久都不会记。
 */
const interruptedStreams = new Set<string>();
let linkDown = false;

function streamLabel(args: unknown[]): string {
  const attrs = args.find((arg) => typeof arg === "object" && arg !== null && !(arg instanceof Error)) as
    | Record<string, unknown>
    | undefined;
  return String(attrs?.["spectrum.stream.label"] ?? "default");
}

function isMessageStream(label: string): boolean {
  return label.startsWith("imessage.messages") || label === "default";
}

export type StreamLogKind = "interrupted" | "repeat" | "recovered" | "other";

/** 确认连接正常（消息流恢复，或者收到了新消息）：清空中断记录，之前是中断状态就发 ok。 */
export function markLinkHealthy(emit: (data: object) => void): void {
  interruptedStreams.clear();
  if (linkDown) {
    linkDown = false;
    emit({ state: "ok" });
  }
}

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
    interruptedStreams.add(label);
    if (!linkDown && isMessageStream(label)) {
      linkDown = true;
      emit({ state: "reconnecting", detail: summarizeArgs(args) });
    }
    return "interrupted";
  }
  if (/stream recovered/.test(text)) {
    if (isMessageStream(label)) {
      // 消息流和投票流走同一条连接，消息流恢复就说明连接恢复了
      const wasInterrupted = interruptedStreams.has(label);
      markLinkHealthy(emit);
      return wasInterrupted ? "recovered" : "other";
    }
    return interruptedStreams.delete(label) ? "recovered" : "other";
  }
  return "other";
}

/** 测试用：清空状态。 */
export function resetLinkState(): void {
  interruptedStreams.clear();
  linkDown = false;
}

export function streamLabelOf(args: unknown[]): string {
  return streamLabel(args);
}

// ---------------------------------------------------------------------------
// 「不支持」警告：SDK 遇到平台不支持的操作（例如撤回投票）时只记一条警告、不抛错，
// 发后不管的操作（编辑、撤回、背景、名片）看起来就像成功了。这里记下这些警告，
// 让动作在执行后检查一下。
// ---------------------------------------------------------------------------

interface UnsupportedNote {
  seq: number;
  subject: string;
  detail: string;
}

const UNSUPPORTED_PATTERN = /does not support (?:content type|action) "([^"]+)"(?:: (.*?))?; skipping\./;
const unsupportedNotes: UnsupportedNote[] = [];
let unsupportedSeq = 0;

/** 如果这条 SDK 日志是「不支持」警告，记下来并返回 true。 */
export function trackUnsupported(args: unknown[]): boolean {
  const text = args.filter((arg) => typeof arg === "string").join(" ");
  const match = UNSUPPORTED_PATTERN.exec(text);
  if (!match) return false;
  unsupportedSeq += 1;
  unsupportedNotes.push({ seq: unsupportedSeq, subject: match[1] ?? "", detail: match[2] ?? "" });
  if (unsupportedNotes.length > 20) unsupportedNotes.shift();
  return true;
}

/** 当前位置，执行动作前取一次。 */
export function unsupportedMark(): number {
  return unsupportedSeq;
}

/** 从 mark 之后有没有关于这些操作（action 名或内容类型）的「不支持」警告；有则返回原因。 */
export function unsupportedSince(mark: number, subjects: string[]): string | undefined {
  const note = unsupportedNotes.find((n) => n.seq > mark && subjects.includes(n.subject));
  return note ? note.detail || `iMessage 不支持 ${note.subject}` : undefined;
}
