/**
 * 排查「消息被吞」：直接向 Photon 要某个聊天在一段时间里的消息，和侧车收到过的对比。
 *
 * spectrum-ts 没有公开查历史消息的接口，这里用的是它内部保存的 Photon 客户端
 * （app.__internal.platforms → imessage → client[].client.messages.listInChat）。
 * 内部结构以后可能变；拿不到时明确报错，不影响收发消息。
 * 只返回时间、方向、字数等，不返回消息内容。
 */

import { BridgeError } from "./protocol.ts";

export interface HistoryMessage {
  guid: string;
  dateCreated: Date;
  isFromMe: boolean;
  isDelivered?: boolean;
  chatGuids?: readonly string[];
  content?: { text?: string; attachments?: readonly unknown[] };
  reaction?: unknown;
  itemType?: unknown;
  isSystemMessage?: boolean;
  isServiceMessage?: boolean;
}

interface ListPage {
  messages: readonly HistoryMessage[];
  nextPageToken?: string;
}

type ListOptions = { after?: Date; before?: Date; isFromMe?: boolean; pageSize?: number; pageToken?: string };

export interface HistoryClient {
  messages: {
    listInChat(chat: string, options: ListOptions): Promise<ListPage>;
    listRecent?(options: ListOptions): Promise<ListPage>;
  };
}

export interface HistoryItem {
  id: string;
  time: number;
  from_me: boolean;
  kind: "text" | "attachment" | "reaction" | "other";
  chars: number;
  attachments: number;
  received: boolean;
}

const PAGE_SIZE = 100;
const MAX_PAGES = 5;

/** 从 Spectrum 实例里找出 Photon 客户端；结构不对就返回空数组。 */
export function photonClients(app: unknown): HistoryClient[] {
  const platforms = (app as { __internal?: { platforms?: Map<string, { client?: unknown }> } })?.__internal?.platforms;
  const entries = platforms?.get?.("imessage")?.client;
  if (!Array.isArray(entries)) return [];
  return entries
    .map((entry) => (entry as { client?: unknown })?.client)
    .filter((client): client is HistoryClient => typeof (client as HistoryClient)?.messages?.listInChat === "function");
}

function kindOf(message: HistoryMessage): HistoryItem["kind"] {
  if (message.reaction) return "reaction";
  if ((message.content?.attachments?.length ?? 0) > 0) return "attachment";
  if (message.content?.text) return "text";
  return "other";
}

/** 列出一个聊天在 [after, before] 里的消息（最多 500 条），标出侧车有没有收到过。 */
export async function listHistory(
  clients: HistoryClient[],
  chatGuid: string,
  after: Date,
  before: Date,
  wasReceived: (id: string) => boolean,
): Promise<{ messages: HistoryItem[]; truncated: boolean }> {
  if (clients.length === 0) {
    throw new BridgeError("UNSUPPORTED", "这个版本的 SDK 拿不到 Photon 的消息记录接口");
  }
  let lastError: unknown;
  for (const client of clients) {
    try {
      const items: HistoryItem[] = [];
      let pageToken: string | undefined;
      let pages = 0;
      do {
        const page = await client.messages.listInChat(chatGuid, { after, before, pageSize: PAGE_SIZE, pageToken });
        for (const message of page.messages) {
          items.push({
            id: message.guid,
            time: new Date(message.dateCreated).getTime(),
            from_me: Boolean(message.isFromMe),
            kind: kindOf(message),
            chars: [...(message.content?.text ?? "")].length,
            attachments: message.content?.attachments?.length ?? 0,
            received: wasReceived(message.guid),
          });
        }
        pageToken = page.nextPageToken || undefined;
        pages += 1;
      } while (pageToken && pages < MAX_PAGES);
      items.sort((a, b) => a.time - b.time);
      return { messages: items, truncated: Boolean(pageToken) };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

export interface RecoverDeps {
  clients: () => HistoryClient[];
  /** 要核对的聊天（chatGuid）：插件启动时告知的已有聊天，加上这次启动后收发过消息的聊天 */
  chats: () => readonly string[];
  wasSeen: (id: string) => boolean;
  /** 用 SDK 的正式接口把整条消息取回来（含附件），交给和实时消息一样的处理流程 */
  deliver: (chatGuid: string, guid: string, lateMs: number) => Promise<void>;
  warn: (message: string) => void;
  info?: (message: string) => void;
  now?: () => number;
}

/**
 * 什么时候去 Photon 核对一次。Photon 是免费服务，按需查询，不定时死查：
 * 刚连上或断线恢复后最容易漏推，查得勤；平时聊着天时偶尔查；没人聊天时很少查。
 */
export interface RecoverSchedule {
  /** 连上或断线恢复后多久之内算「刚重连」 */
  reconnectWindowMs: number;
  reconnectIntervalMs: number;
  /** 最近一条消息之后多久之内算「正在聊天」 */
  activeWindowMs: number;
  activeIntervalMs: number;
  idleIntervalMs: number;
  /** 查询失败后逐次加倍等待，最长等这么久 */
  maxBackoffMs: number;
}

const MINUTE = 60_000;

export const DEFAULT_RECOVER_SCHEDULE: RecoverSchedule = {
  reconnectWindowMs: 30 * MINUTE,
  reconnectIntervalMs: 2 * MINUTE,
  activeWindowMs: 120 * MINUTE,
  activeIntervalMs: 10 * MINUTE,
  idleIntervalMs: 30 * MINUTE,
  maxBackoffMs: 30 * MINUTE,
};

/**
 * 漏消息自动补收。真机发现：Photon 断线恢复后约 30 秒内对方发的两条消息没有推送过来，
 * 而 SDK 只在「连接断了」时补拉，连接看着正常就不会补。这里按需对一下 Photon 上对方最近发的消息，
 * 侧车没收到的取回来补上。只补侧车这次启动之后的消息；刚发出的消息先留给实时推送（graceMs）。
 *
 * 按聊天逐个查（listInChat）。不用 listRecent：它不带聊天编号，Photon 没法把请求路由到号码所在的实例，
 * 真机上每次都返回「No instance routed for this request」，这个功能从来没成功过。
 */
export class MissedMessageRecoverer {
  private readonly deps: RecoverDeps;
  private readonly startedAt: number;
  private readonly schedule: RecoverSchedule;
  private readonly graceMs: number;
  private readonly overlapMs: number;
  private readonly now: () => number;
  private checkedUntil: number;
  private lastCheckAt: number;
  private lastActivityAt: number;
  private lastReconnectAt: number;
  private failures = 0;
  private retryAt = 0;
  private running = false;
  private disabled = false;

  constructor(
    deps: RecoverDeps,
    startedAt: number,
    schedule: RecoverSchedule = DEFAULT_RECOVER_SCHEDULE,
    graceMs = 20_000,
    overlapMs = 120_000,
  ) {
    this.deps = deps;
    this.startedAt = startedAt;
    this.schedule = schedule;
    this.graceMs = graceMs;
    this.overlapMs = overlapMs;
    this.now = deps.now ?? Date.now;
    this.checkedUntil = startedAt;
    this.lastCheckAt = startedAt;
    this.lastActivityAt = Number.NEGATIVE_INFINITY;
    // 侧车刚启动也是一次新连接
    this.lastReconnectAt = startedAt;
  }

  /** 收到或补收了一条消息：接下来一段时间算「正在聊天」。 */
  noteActivity(): void {
    this.lastActivityAt = this.now();
  }

  /** 与 Photon 的连接断开后恢复：接下来一段时间查得勤一些。 */
  noteReconnect(): void {
    this.lastReconnectAt = this.now();
  }

  /** 现在距上次核对应该隔多久。 */
  intervalNow(): number {
    const now = this.now();
    if (now - this.lastReconnectAt < this.schedule.reconnectWindowMs) return this.schedule.reconnectIntervalMs;
    if (now - this.lastActivityAt < this.schedule.activeWindowMs) return this.schedule.activeIntervalMs;
    return this.schedule.idleIntervalMs;
  }

  /** 定时调用（间隔取最短的那档）；没到时间什么也不做，到了才去 Photon 查。 */
  async tick(): Promise<number> {
    if (this.running || this.disabled) return 0;
    const now = this.now();
    if (now < this.retryAt) return 0;
    if (this.failures === 0 && now - this.lastCheckAt < this.intervalNow()) return 0;
    return this.check();
  }

  /** 查一次，返回补收了几条。出错只记日志，按退避时间再试。 */
  async check(): Promise<number> {
    if (this.running || this.disabled) return 0;
    const client = this.deps.clients()[0];
    if (!client) {
      this.disabled = true;
      this.deps.warn("这个版本的 SDK 拿不到 Photon 的消息记录接口，漏消息自动补收已停用（不影响正常收发）");
      return 0;
    }
    const now = this.now();
    const chats = [...new Set(this.deps.chats())];
    // 还不知道有哪些聊天（没有聊过）：不发请求
    if (chats.length === 0) {
      this.lastCheckAt = now;
      return 0;
    }
    const after = Math.max(this.startedAt, this.checkedUntil - this.overlapMs);
    const before = now - this.graceMs;
    if (before <= after) return 0;
    this.running = true;
    let recovered = 0;
    try {
      const missing: { chat: string; message: HistoryMessage }[] = [];
      for (const chat of chats) {
        let pageToken: string | undefined;
        let pages = 0;
        do {
          const page = await client.messages.listInChat(chat, {
            after: new Date(after),
            before: new Date(before),
            pageSize: PAGE_SIZE,
            pageToken,
          });
          for (const message of page.messages) {
            if (isRecoverable(message, this.startedAt) && !this.deps.wasSeen(message.guid)) missing.push({ chat, message });
          }
          pageToken = page.nextPageToken || undefined;
          pages += 1;
        } while (pageToken && pages < MAX_PAGES);
      }
      missing.sort((a, b) => new Date(a.message.dateCreated).getTime() - new Date(b.message.dateCreated).getTime());
      for (const { chat, message } of missing) {
        if (this.deps.wasSeen(message.guid)) continue; // 查的这会儿实时推送刚好到了
        await this.deps.deliver(message.chatGuids?.[0] ?? chat, message.guid, this.now() - new Date(message.dateCreated).getTime());
        recovered += 1;
      }
      this.checkedUntil = before;
      this.lastCheckAt = now;
      if (this.failures > 0) {
        (this.deps.info ?? this.deps.warn)(`漏消息自动补收恢复正常（之前连续 ${this.failures} 次没查成）`);
      }
      this.failures = 0;
      this.retryAt = 0;
    } catch (error) {
      this.failures += 1;
      this.lastCheckAt = now;
      const wait = Math.min(this.schedule.reconnectIntervalMs * 2 ** (this.failures - 1), this.schedule.maxBackoffMs);
      this.retryAt = now + wait;
      // 连续失败只在第一次记日志，恢复时再记一行，免得每次重试都刷屏
      if (this.failures === 1) {
        this.deps.warn(
          `漏消息自动补收这次没查成，之后放慢重试（最长 ${Math.round(this.schedule.maxBackoffMs / MINUTE)} 分钟一次），` +
            `恢复时会再提示：${String((error as Error)?.message ?? error)}`,
        );
      }
    } finally {
      this.running = false;
    }
    return recovered;
  }
}

/** 对方发的普通消息（有文字或附件）；点按回应、系统消息不补（点按回应走另一条推送）。 */
function isRecoverable(message: HistoryMessage, startedAt: number): boolean {
  if (message.isFromMe || message.reaction || message.isSystemMessage || message.isServiceMessage) return false;
  if (new Date(message.dateCreated).getTime() < startedAt) return false;
  return Boolean(message.content?.text) || (message.content?.attachments?.length ?? 0) > 0;
}
