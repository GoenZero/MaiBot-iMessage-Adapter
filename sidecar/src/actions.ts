/**
 * 出站动作：发送、引用回复、点赞、编辑、撤回、输入中、标记已读。
 *
 * 每个动作都以 SDK 的实际调用结果为准：
 * - SDK 抛错 → 失败
 * - SDK 返回空（内容被判定为不支持、跳过）→ 也算失败（NOT_DELIVERED）
 * - 成功时返回 iMessage 消息编号，并把消息对象存进句柄缓存，供以后引用、撤回
 */
import { Emoji, attachment, markdown, text } from "@spectrum-ts/core";

import type { MessageLike } from "./convert.ts";
import type { LruCache } from "./handles.ts";
import { readOutboundFile } from "./media.ts";
import { BridgeError, optionalString, requireString } from "./protocol.ts";

/** 可操作的消息对象（SDK Message 的最小形状）。 */
export interface ActionMessage extends MessageLike {
  reply?(...content: unknown[]): Promise<unknown>;
  react?(reaction: string): Promise<unknown>;
  read?(): Promise<void>;
  edit?(content: unknown): Promise<void>;
  unsend?(): Promise<void>;
}

/** 对话对象（SDK Space 的最小形状）。 */
export interface SpaceLike {
  id: string;
  send(...content: unknown[]): Promise<unknown>;
  startTyping(): Promise<void>;
  stopTyping(): Promise<void>;
  getMessage(id: string): Promise<unknown>;
}

/** 内容构造函数，默认使用 SDK 的实现；测试时可替换。 */
export interface Builders {
  text(value: string): unknown;
  markdown(value: string): unknown;
  attachment(data: Buffer, options: { mimeType: string; name?: string }): unknown;
}

export const sdkBuilders: Builders = {
  text: (value) => text(value),
  markdown: (value) => markdown(value),
  attachment: (data, options) => attachment(data, options),
};

/** iMessage 的六种点赞（Tapback）。 */
export const TAPBACKS: Record<string, string> = {
  love: Emoji.love,
  like: Emoji.like,
  dislike: Emoji.dislike,
  laugh: Emoji.laugh,
  emphasize: Emoji.emphasize,
  question: Emoji.question,
};

export interface ActionDeps {
  resolveSpace(chatId: string): Promise<SpaceLike>;
  handles: LruCache<ActionMessage>;
  mediaDir: string;
  maxAttachmentBytes: number;
  builders: Builders;
  warn(message: string): void;
}

interface OutPart {
  type: string;
  text?: unknown;
  markdown?: unknown;
  path?: unknown;
  mime?: unknown;
  name?: unknown;
}

function isMessage(value: unknown): value is ActionMessage {
  return typeof value === "object" && value !== null && typeof (value as { id?: unknown }).id === "string";
}

function toMessageList(result: unknown): ActionMessage[] {
  const list = Array.isArray(result) ? result : [result];
  return list.filter(isMessage);
}

export class Actions {
  private readonly deps: ActionDeps;

  constructor(deps: ActionDeps) {
    this.deps = deps;
  }

  /** 先查句柄缓存，再请 SDK 按编号查找；都找不到返回 undefined。 */
  async findMessage(space: SpaceLike, messageId: string): Promise<ActionMessage | undefined> {
    const cached = this.deps.handles.get(messageId);
    if (cached) return cached;
    try {
      const fetched = await space.getMessage(messageId);
      if (isMessage(fetched)) {
        this.deps.handles.set(fetched.id, fetched);
        return fetched;
      }
    } catch (error) {
      this.deps.warn(`按编号查找消息失败（${messageId}）：${String((error as Error).message ?? error)}`);
    }
    return undefined;
  }

  private async buildContents(rawParts: unknown): Promise<unknown[]> {
    if (!Array.isArray(rawParts) || rawParts.length === 0) throw new BridgeError("BAD_REQUEST", "parts 不能为空");
    const contents: unknown[] = [];
    for (const raw of rawParts as OutPart[]) {
      if (raw?.type === "text") {
        const value = typeof raw.text === "string" ? raw.text : "";
        if (!value.trim()) continue;
        contents.push(raw.markdown === true ? this.deps.builders.markdown(value) : this.deps.builders.text(value));
      } else if (raw?.type === "file") {
        if (typeof raw.path !== "string" || typeof raw.mime !== "string") {
          throw new BridgeError("BAD_REQUEST", "文件片段缺少 path 或 mime");
        }
        const data = await readOutboundFile(raw.path, this.deps.mediaDir, this.deps.maxAttachmentBytes);
        const options: { mimeType: string; name?: string } = { mimeType: raw.mime };
        if (typeof raw.name === "string" && raw.name) options.name = raw.name;
        contents.push(this.deps.builders.attachment(data, options));
      } else {
        throw new BridgeError("BAD_REQUEST", `不认识的出站片段类型：${String(raw?.type)}`);
      }
    }
    if (contents.length === 0) throw new BridgeError("BAD_REQUEST", "没有可发送的内容");
    return contents;
  }

  /** 发送消息；带 reply_to 时以引用回复的形式发送。 */
  async send(args: Record<string, unknown>): Promise<{ message_ids: string[]; quoted: boolean; partial: boolean }> {
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    const contents = await this.buildContents(args.parts);
    const replyTo = optionalString(args, "reply_to");

    let target: ActionMessage | undefined;
    if (replyTo) {
      target = await this.findMessage(space, replyTo);
      if (!target?.reply) this.deps.warn(`找不到要引用的消息 ${replyTo}，改为普通发送`);
    }

    const result = target?.reply ? await target.reply(...contents) : await space.send(...contents);
    const sent = toMessageList(result);
    if (sent.length === 0) {
      throw new BridgeError("NOT_DELIVERED", "Photon 没有返回消息编号，内容可能被判定为不支持而跳过");
    }
    for (const message of sent) this.deps.handles.set(message.id, message);
    return {
      message_ids: sent.map((message) => message.id),
      quoted: Boolean(target?.reply),
      partial: sent.length < contents.length,
    };
  }

  /** 给某条消息点赞（Tapback）。 */
  async react(args: Record<string, unknown>): Promise<{ reaction_id: string }> {
    const kind = requireString(args, "kind");
    const emoji = TAPBACKS[kind];
    if (!emoji) throw new BridgeError("BAD_REQUEST", `不支持的点赞类型：${kind}`);
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    const messageId = requireString(args, "message_id");
    const target = await this.findMessage(space, messageId);
    if (!target?.react) throw new BridgeError("MESSAGE_NOT_FOUND", `找不到消息 ${messageId}，可能太久远或适配器重启过`);
    const reaction = await target.react(emoji);
    if (!isMessage(reaction)) throw new BridgeError("NOT_DELIVERED", "Photon 没有返回点赞结果");
    this.deps.handles.set(reaction.id, reaction);
    return { reaction_id: reaction.id };
  }

  /** 找到一条自己发出的消息；找不到或不是自己发的都报错。 */
  private async findOwnMessage(args: Record<string, unknown>, verb: string): Promise<ActionMessage> {
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    const messageId = requireString(args, "message_id");
    const target = await this.findMessage(space, messageId);
    if (!target) throw new BridgeError("MESSAGE_NOT_FOUND", `找不到消息 ${messageId}，可能太久远或适配器重启过`);
    if (target.direction !== "outbound") throw new BridgeError("NOT_OWN_MESSAGE", `只能${verb}自己发出的消息`);
    return target;
  }

  /** 修改自己发出的一条文字消息（iMessage 限 15 分钟内、最多 5 次，超出时由 Photon 报错）。 */
  async edit(args: Record<string, unknown>): Promise<Record<string, never>> {
    const value = requireString(args, "text");
    const target = await this.findOwnMessage(args, "编辑");
    if (!target.edit) throw new BridgeError("UNSUPPORTED", "这条消息不能编辑");
    await target.edit(args.markdown === true ? this.deps.builders.markdown(value) : this.deps.builders.text(value));
    return {};
  }

  /** 撤回自己发出的一条消息或一个点赞（消息限 2 分钟内，点赞不限时）。 */
  async unsend(args: Record<string, unknown>): Promise<Record<string, never>> {
    const target = await this.findOwnMessage(args, "撤回");
    if (!target.unsend) throw new BridgeError("UNSUPPORTED", "这条消息不能撤回");
    await target.unsend();
    this.deps.handles.delete(target.id);
    return {};
  }

  /** 开始或停止「正在输入」。 */
  async typing(args: Record<string, unknown>): Promise<Record<string, never>> {
    const state = requireString(args, "state");
    if (state !== "start" && state !== "stop") throw new BridgeError("BAD_REQUEST", "state 只能是 start 或 stop");
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    if (state === "start") await space.startTyping();
    else await space.stopTyping();
    return {};
  }

  /** 把对话标为已读（iMessage 会把整个对话一起标已读）。 */
  async markRead(args: Record<string, unknown>): Promise<Record<string, never>> {
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    const messageId = requireString(args, "message_id");
    const target = await this.findMessage(space, messageId);
    if (!target?.read) throw new BridgeError("MESSAGE_NOT_FOUND", `找不到消息 ${messageId}`);
    await target.read();
    return {};
  }
}
