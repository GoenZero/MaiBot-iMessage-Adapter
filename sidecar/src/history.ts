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
  content?: { text?: string; attachments?: readonly unknown[] };
  reaction?: unknown;
  itemType?: unknown;
}

interface ListPage {
  messages: readonly HistoryMessage[];
  nextPageToken?: string;
}

export interface HistoryClient {
  messages: {
    listInChat(chat: string, options: { after?: Date; before?: Date; pageSize?: number; pageToken?: string }): Promise<ListPage>;
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
