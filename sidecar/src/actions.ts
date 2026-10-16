/**
 * 出站动作：发送（含特效、投票、链接预览）、引用回复、点赞、编辑、撤回、聊天背景、分享名片、
 * 贴贴纸、输入中、标记已读。
 *
 * 每个动作都以 SDK 的实际调用结果为准：
 * - SDK 抛错 → 失败
 * - SDK 返回空（内容被判定为不支持、跳过）→ 也算失败（NOT_DELIVERED）
 * - 成功时返回 iMessage 消息编号，并把消息对象存进句柄缓存，供以后引用、撤回
 */
import { Emoji, attachment, markdown, poll, richlink, text } from "@spectrum-ts/core";
import { background, effect, nativeContactCard } from "@spectrum-ts/imessage";

import type { MessageLike } from "./convert.ts";
import type { LruCache } from "./handles.ts";
import { readOutboundFile } from "./media.ts";
import { BridgeError, optionalString, requireString } from "./protocol.ts";
import { rememberSentPoll } from "./polls.ts";
import { Lanes } from "./lanes.ts";
import { unsupportedMark, unsupportedSince } from "./sdklog.ts";

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
  /** 这个对话用的 Photon 号码（共享号码时是 "shared"） */
  phone?: string;
  send(...content: unknown[]): Promise<unknown>;
  startTyping(): Promise<void>;
  stopTyping(): Promise<void>;
  getMessage(id: string): Promise<unknown>;
}

/** iMessage 消息特效：名称 → Apple 的特效编号。前 8 种是全屏特效，后 5 种是气泡特效。 */
export const EFFECTS = {
  balloons: "com.apple.messages.effect.CKBalloonEffect",
  confetti: "com.apple.messages.effect.CKConfettiEffect",
  fireworks: "com.apple.messages.effect.CKFireworksEffect",
  celebration: "com.apple.messages.effect.CKHappyBirthdayEffect",
  heart: "com.apple.messages.effect.CKHeartEffect",
  lasers: "com.apple.messages.effect.CKLasersEffect",
  sparkles: "com.apple.messages.effect.CKSparklesEffect",
  spotlight: "com.apple.messages.effect.CKSpotlightEffect",
  echo: "com.apple.messages.effect.CKEchoEffect",
  slam: "com.apple.MobileSMS.expressivesend.impact",
  loud: "com.apple.MobileSMS.expressivesend.loud",
  gentle: "com.apple.MobileSMS.expressivesend.gentle",
  invisible: "com.apple.MobileSMS.expressivesend.invisibleink",
} as const;
export type EffectName = keyof typeof EFFECTS;

export const MAX_POLL_OPTIONS = 12;

/** 内容构造函数，默认使用 SDK 的实现；测试时可替换。 */
export interface Builders {
  text(value: string): unknown;
  markdown(value: string): unknown;
  attachment(data: Buffer, options: { mimeType: string; name?: string }): unknown;
  effect(content: unknown, name: EffectName): unknown;
  poll(title: string, options: string[]): unknown;
  richlink(url: string): unknown;
  background(data: Buffer | "clear", mimeType?: string): unknown;
  contactCard(): unknown;
}

export const sdkBuilders: Builders = {
  text: (value) => text(value),
  markdown: (value) => markdown(value),
  attachment: (data, options) => attachment(data, options),
  effect: (content, name) => effect(content as Parameters<typeof effect>[0], EFFECTS[name]),
  poll: (title, options) => poll(title, options),
  richlink: (url) => richlink(url),
  background: (data, mimeType) => (data === "clear" ? background("clear") : background(data, { mimeType: mimeType ?? "image/jpeg" })),
  contactCard: () => nativeContactCard(),
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

/** 贴贴纸：上传图片并贴在某条消息上。由打包补丁登记在 globalThis 上（见 build-config.mjs）。 */
export type PlaceSticker = (args: {
  spaceId: string;
  phone: string | undefined;
  messageId: string;
  data: Uint8Array;
  fileName: string;
  placement: { x: number; y: number; scale?: number; rotation?: number };
}) => Promise<{ id?: string }>;

export function bundledPlaceSticker(): PlaceSticker | undefined {
  const hook = (globalThis as { __imessagePlaceSticker?: unknown }).__imessagePlaceSticker;
  return typeof hook === "function" ? (hook as PlaceSticker) : undefined;
}

/** 需要检查「不支持」警告的动作最多独占多久（毫秒）；比 Python 端最长的等待（60 秒）略长 */
const CHECKED_HOLD_LIMIT_MS = 90_000;

export interface ActionDeps {
  resolveSpace(chatId: string): Promise<SpaceLike>;
  /** 测试时可替换；默认用打包补丁登记的实现 */
  placeSticker?: () => PlaceSticker | undefined;
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
  effect?: unknown;
  path?: unknown;
  mime?: unknown;
  name?: unknown;
  title?: unknown;
  options?: unknown;
  url?: unknown;
}

function isEffectName(value: unknown): value is EffectName {
  return typeof value === "string" && Object.hasOwn(EFFECTS, value);
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

  private readonly checked = new Lanes(CHECKED_HOLD_LIMIT_MS);

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
        const content = raw.markdown === true ? this.deps.builders.markdown(value) : this.deps.builders.text(value);
        if (raw.effect === undefined) {
          contents.push(content);
        } else if (isEffectName(raw.effect)) {
          contents.push(this.deps.builders.effect(content, raw.effect));
        } else {
          throw new BridgeError("BAD_REQUEST", `不认识的特效：${String(raw.effect)}`);
        }
      } else if (raw?.type === "poll") {
        const title = typeof raw.title === "string" ? raw.title.trim() : "";
        const options = Array.isArray(raw.options)
          ? raw.options.filter((o): o is string => typeof o === "string" && Boolean(o.trim())).map((o) => o.trim())
          : [];
        if (!title) throw new BridgeError("BAD_REQUEST", "投票缺少标题");
        if (options.length < 2 || options.length > MAX_POLL_OPTIONS) {
          throw new BridgeError("BAD_REQUEST", `投票要有 2 到 ${MAX_POLL_OPTIONS} 个选项`);
        }
        contents.push(this.deps.builders.poll(title, options));
      } else if (raw?.type === "link") {
        if (typeof raw.url !== "string" || !/^https?:\/\/\S+$/.test(raw.url)) throw new BridgeError("BAD_REQUEST", "链接格式不对");
        contents.push(this.deps.builders.richlink(raw.url));
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
    let replyTo = optionalString(args, "reply_to");
    if (replyTo && Array.isArray(args.parts) && args.parts.some((part) => (part as OutPart | null)?.type === "poll")) {
      // iMessage 的投票不能以引用回复的形式发出
      replyTo = undefined;
    }

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
    // 记下 AI 发起的投票的标题：Photon 送回来的投票事件没有标题（见 polls.ts）
    if (Array.isArray(args.parts) && sent.length === args.parts.length) {
      (args.parts as OutPart[]).forEach((part, index) => {
        const message = sent[index];
        if (part?.type === "poll" && message && typeof part.title === "string") rememberSentPoll(message.id, part.title.trim());
      });
    }
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

  /**
   * 执行一个「发后不管」的操作，并检查 SDK 有没有把它当成不支持而跳过。
   * SDK 对不支持的操作只记警告不抛错，不检查的话会误报成功。
   */
  private async fireAndCheck(subjects: string[], run: () => Promise<unknown>): Promise<void> {
    // 「不支持」警告是全局的，分不清是哪个动作发的；所以这类动作一次只执行一个，
    // 否则一个动作的警告会被算到同时进行的另一个头上（外部审查发现）
    await this.checked.run("check", async () => {
      const mark = unsupportedMark();
      await run();
      const reason = unsupportedSince(mark, subjects);
      if (reason) throw new BridgeError("UNSUPPORTED", reason);
    });
  }

  /** 修改自己发出的一条文字消息（iMessage 限 15 分钟内、最多 5 次，超出时由 Photon 报错）。改后按纯文字显示。 */
  async edit(args: Record<string, unknown>): Promise<Record<string, never>> {
    const value = requireString(args, "text");
    const target = await this.findOwnMessage(args, "编辑");
    const kind = target.content?.type;
    if (!target.edit || (kind !== "text" && kind !== "markdown")) throw new BridgeError("UNSUPPORTED", "只能编辑文字消息");
    await this.fireAndCheck(["edit"], () => target.edit!(this.deps.builders.text(value)));
    return {};
  }

  /** 撤回自己发出的一条消息或一个点赞（消息限 2 分钟内，点赞不限时；投票不能撤回）。 */
  async unsend(args: Record<string, unknown>): Promise<Record<string, never>> {
    const target = await this.findOwnMessage(args, "撤回");
    if (target.content?.type === "poll") throw new BridgeError("UNSUPPORTED", "iMessage 的投票不能撤回");
    if (!target.unsend) throw new BridgeError("UNSUPPORTED", "这条消息不能撤回");
    await this.fireAndCheck(["unsend"], () => target.unsend!());
    this.deps.handles.delete(target.id);
    return {};
  }

  /** 设置或清除聊天背景。设置时用这个对话里某条消息里的图片。 */
  async background(args: Record<string, unknown>): Promise<Record<string, never>> {
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    const action = requireString(args, "action");
    if (action === "clear") {
      await this.fireAndCheck(["background"], () => space.send(this.deps.builders.background("clear")));
      return {};
    }
    if (action !== "set") throw new BridgeError("BAD_REQUEST", "action 只能是 set 或 clear");
    const messageId = requireString(args, "message_id");
    const message = await this.findMessage(space, messageId);
    if (!message) throw new BridgeError("MESSAGE_NOT_FOUND", `找不到消息 ${messageId}，可能太久远或适配器重启过`);
    const image = findImage(message.content);
    if (!image) throw new BridgeError("NO_IMAGE", "这条消息里没有图片");
    const data = await image.read();
    if (data.length > this.deps.maxAttachmentBytes) throw new BridgeError("TOO_LARGE", "图片太大");
    await this.fireAndCheck(["background"], () => space.send(this.deps.builders.background(data, image.mimeType)));
    return {};
  }

  /** 把机器人账号自己的名字和头像分享给对方（Apple 的「共享名字与照片」）。 */
  async shareContact(args: Record<string, unknown>): Promise<Record<string, never>> {
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    await this.fireAndCheck(["contactCard"], () => space.send(this.deps.builders.contactCard()));
    return {};
  }

  /**
   * 把一张图片当贴纸贴在对方的某条消息上（iMessage 长按消息 → 贴纸那种）。
   * 图片来自 Python 写的临时文件（AI 从 MaiBot 表情包库挑的）。位置默认贴在气泡中间。
   */
  async placeSticker(args: Record<string, unknown>): Promise<{ sticker_id?: string }> {
    const place = (this.deps.placeSticker ?? bundledPlaceSticker)();
    if (!place) throw new BridgeError("UNSUPPORTED", "这个版本的侧车不支持贴贴纸");
    const space = await this.deps.resolveSpace(requireString(args, "chat_id"));
    const messageId = requireString(args, "message_id");
    const path = requireString(args, "path");
    const target = await this.findMessage(space, messageId);
    if (!target) throw new BridgeError("MESSAGE_NOT_FOUND", `找不到消息 ${messageId}，可能太久远或适配器重启过`);
    const data = await readOutboundFile(path, this.deps.mediaDir, this.deps.maxAttachmentBytes);
    const name = optionalString(args, "name") ?? "sticker.png";
    const placed = await place({
      spaceId: space.id,
      phone: space.phone,
      messageId: target.id,
      data: new Uint8Array(data.buffer, data.byteOffset, data.byteLength),
      fileName: name,
      placement: { x: 0.5, y: 0.5 },
    });
    return placed?.id ? { sticker_id: placed.id } : {};
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

interface ReadableImage {
  mimeType: string;
  read(): Promise<Buffer>;
}

/** 在一条消息的内容里找第一张图片（普通图片附件，或组合消息、引用回复里的图片）。 */
export function findImage(content: unknown, depth = 0): ReadableImage | undefined {
  if (depth > 4 || typeof content !== "object" || content === null) return undefined;
  const record = content as Record<string, unknown>;
  if (record.type === "attachment" && typeof record.mimeType === "string" && record.mimeType.startsWith("image/")) {
    const read = record.read;
    if (typeof read === "function") return { mimeType: record.mimeType, read: () => (read as () => Promise<Buffer>).call(record) };
  }
  if (record.type === "group" && Array.isArray(record.items)) {
    for (const item of record.items) {
      const found = findImage((item as { content?: unknown } | null)?.content, depth + 1);
      if (found) return found;
    }
  }
  if (record.type === "reply" || record.type === "effect") return findImage(record.content, depth + 1);
  return undefined;
}
