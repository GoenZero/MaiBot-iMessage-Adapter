/**
 * 入站消息转换：把 SDK 的 Message 翻译成发给 Python 的简单 JSON。
 *
 * 硬性规则：任何一种内容都必须有去处，不能整条丢弃。
 * - 普通内容（文字、图片、文件、名片、链接、投票卡片……）→ `message` 事件
 * - 控制类事件（对方点赞、已读、投票选择……）→ `control` 事件，不伪装成用户发言
 * - 不认识的内容 → `unsupported` 片段，由 Python 变成可读的占位文字
 *
 * 这里只依赖消息的「形状」，不依赖 SDK 的具体类，方便用假对象做单元测试。
 */
import { sentPollTitle, UNTITLED_POLL } from "./polls.ts";

/** SDK 内容对象的最小形状。 */
export interface ContentLike {
  type: string;
  [key: string]: unknown;
}

/** SDK 消息对象的最小形状。 */
export interface MessageLike {
  id: string;
  content: ContentLike;
  direction?: string;
  sender?: { id: string } | undefined;
  timestamp?: Date;
  space?: { id: string; type?: string };
  // iMessage 平台附加在消息上的字段（spectrum-ts 把它们直接放在消息对象上）
  /** 气泡、屏幕特效的编号；对方用了特效时才有 */
  expressiveSendStyleId?: string;
  /** 文字格式：粗体、斜体……以及 iOS 18 文字效果（effectName） */
  formatting?: readonly { type?: string; effectName?: string; start: number; length: number }[];
  /** 原始正文（图片等附件处是占位符 U+FFFC）；文字效果的位置按它算 */
  nativeText?: string;
  /** 每个附件的元数据，isSticker 表示是贴纸 */
  attachmentMetadata?: readonly { guid?: string; isSticker?: boolean }[];
}

export type InboundPart =
  | { type: "text"; text: string }
  | { type: "image"; path: string; mime: string; name: string; size: number; sticker?: boolean }
  | { type: "sticker" }
  | { type: "file"; mime: string; name: string; size?: number; note?: string }
  | { type: "text_file"; path: string; mime: string; name: string; size: number }
  | { type: "voice"; id?: string; mime: string; duration?: number; size?: number; path?: string; note?: string }
  | { type: "text_effect"; effect: string; text: string }
  | { type: "contact"; name?: string; phones: string[]; emails: string[]; org?: string; urls: string[] }
  | { type: "link"; url: string }
  | { type: "app"; url?: string; caption?: string }
  | { type: "poll"; title: string; options: string[] }
  | { type: "effect"; effect: string }
  | { type: "unsupported"; what: string };

export interface ReplyRef {
  message_id: string;
  text?: string;
}

export interface InboundMessage {
  message_id: string;
  chat_id: string;
  chat_type: string;
  sender_id: string;
  timestamp: number;
  parts: InboundPart[];
  /** false 表示这条消息的编号不能用来引用、点赞（例如贴在消息上的贴纸） */
  quotable?: boolean;
  reply_to?: ReplyRef;
}

export type ControlEvent =
  | { type: "reaction"; emoji: string; target_id: string; target_text?: string; target_from_me?: boolean }
  | { type: "read"; target_id: string }
  | { type: "poll_vote"; poll_title: string; option: string; selected: boolean }
  | { type: "edited"; target_id: string; text: string; old_text?: string; target_from_me?: boolean }
  | { type: "unsent"; target_id: string; old_text?: string; target_from_me?: boolean }
  | { type: "reaction_removed"; emoji: string; target_id: string; target_text?: string; target_from_me?: boolean }
  | { type: "other"; content_type: string };

export interface InboundControl {
  event_id: string;
  chat_id: string;
  sender_id: string;
  timestamp: number;
  event: ControlEvent;
}

export type InboundResult =
  | { kind: "message"; data: InboundMessage }
  | { kind: "control"; data: InboundControl }
  | { kind: "echo" };

/** 保存图片附件的回调；失败时抛异常，由转换器降级成文件说明。 */
export type SaveImage = (content: ContentLike) => Promise<{ path: string; size: number }>;

export interface ConvertOptions {
  saveImage: SaveImage;
  maxImageBytes: number;
  /** 下载文本类文件（.md、.txt 等）的回调；不提供则只告诉 AI 文件名。 */
  saveTextFile?: SaveImage;
  maxTextFileBytes?: number;
  /** 下载语音的回调（开启「语音转文字」时才提供）；不提供则只告诉 AI 收到了语音。 */
  saveVoice?: SaveImage;
  /** 语音下载失败后的重试间隔（毫秒），测试时可设为 0。 */
  voiceRetryDelayMs?: number;
  /** 每次下载语音的最长等待（毫秒）。下载期间后面的消息都要排队，所以要有上限。 */
  voiceTimeoutMs?: number;
  /** 这条消息里哪些附件是贴纸（按附件编号）；由 convertInbound 按消息的附加字段填写。 */
  stickerIds?: ReadonlySet<string>;
  /** 按编号查已缓存的消息（对方编辑、撤回时，用来告诉 AI 原来那句是什么）。 */
  lookupMessage?: (id: string) => MessageLike | undefined;
  /** 记录警告日志（下载失败等），不影响转换结果。 */
  warn: (message: string) => void;
}

const TEXT_EXTENSIONS = new Set([
  ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log", ".yaml", ".yml", ".toml", ".ini", ".xml",
  ".html", ".htm", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".sh", ".sql", ".srt",
]);
const TEXT_MIMES = new Set(["application/json", "application/xml", "application/x-yaml", "application/toml", "application/x-sh"]);

/** 是否是可以直接当文字读的文件。 */
export function isTextLike(mime: string, name: string): boolean {
  if (mime.startsWith("text/") && !mime.includes("vcard") && !mime.includes("directory")) return true;
  if (TEXT_MIMES.has(mime)) return true;
  const dot = name.lastIndexOf(".");
  return dot >= 0 && TEXT_EXTENSIONS.has(name.slice(dot).toLowerCase());
}

/** 这些内容类型不是用户「说的话」，而是事件。 */
const CONTROL_TYPES = new Set([
  "reaction",
  "read",
  "poll_option",
  "typing",
  "edit",
  "unsend",
  "rename",
  "avatar",
  "addMember",
  "removeMember",
  "leaveSpace",
]);

const MAX_DEPTH = 4;

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function asContent(value: unknown): ContentLike | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const type = (value as { type?: unknown }).type;
  return typeof type === "string" ? (value as ContentLike) : undefined;
}

function asMessage(value: unknown): MessageLike | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const record = value as { id?: unknown; content?: unknown };
  if (typeof record.id !== "string") return undefined;
  const content = asContent(record.content);
  return content ? (value as MessageLike) : undefined;
}

/** 给被引用的消息生成一句简短预览，供 AI 知道引用的是哪句话。 */
export function previewText(content: ContentLike | undefined, depth = 0): string | undefined {
  if (!content || depth > MAX_DEPTH) return undefined;
  switch (content.type) {
    case "text":
      return str(content.text);
    case "markdown":
      return str(content.markdown);
    case "reply":
    case "effect":
      return previewText(asContent(content.content), depth + 1);
    case "group": {
      const items = Array.isArray(content.items) ? content.items : [];
      const texts = items
        .map((item) => previewText(asMessage(item)?.content, depth + 1))
        .filter((text): text is string => Boolean(text));
      return texts.length ? texts.join(" ") : "[图片或附件]";
    }
    case "attachment":
      return String(content.mimeType ?? "").startsWith("image/") ? "[图片]" : "[文件]";
    case "voice":
      return "[语音]";
    case "contact":
      return "[联系人名片]";
    case "richlink":
      return str(content.url);
    case "poll":
      return `[投票] ${str(content.title) ?? ""}`.trim();
    default:
      return undefined;
  }
}

function contactPart(content: ContentLike): InboundPart {
  const name = content.name as { formatted?: unknown; first?: unknown; last?: unknown } | undefined;
  const formatted =
    str(name?.formatted) ?? ([str(name?.last), str(name?.first)].filter(Boolean).join("") || undefined);
  const values = (list: unknown): string[] =>
    Array.isArray(list)
      ? list.map((item) => str((item as { value?: unknown } | null)?.value)).filter((v): v is string => Boolean(v))
      : [];
  const org = content.org as { name?: unknown } | undefined;
  const urls = Array.isArray(content.urls) ? content.urls.filter((u): u is string => typeof u === "string") : [];
  const part: InboundPart = {
    type: "contact",
    phones: values(content.phones),
    emails: values(content.emails),
    urls,
  };
  if (formatted) part.name = formatted;
  const orgName = str(org?.name);
  if (orgName) part.org = orgName;
  return part;
}

async function appPart(content: ContentLike): Promise<InboundPart> {
  const part: InboundPart = { type: "app" };
  try {
    if (typeof content.url === "function") part.url = String(await (content.url as () => Promise<unknown>)());
    if (typeof content.layout === "function") {
      const layout = (await (content.layout as () => Promise<unknown>)()) as { caption?: unknown } | undefined;
      const caption = str(layout?.caption);
      if (caption) part.caption = caption;
    }
  } catch {
    // 卡片详情拿不到也没关系，至少告诉 AI 收到了一张卡片
  }
  return part;
}

/** 把一个内容对象转换成若干片段，并找出它引用的消息（如果有）。 */
async function convertContent(
  content: ContentLike,
  options: ConvertOptions,
  depth: number,
): Promise<{ parts: InboundPart[]; replyTo?: ReplyRef }> {
  if (depth > MAX_DEPTH) return { parts: [{ type: "unsupported", what: "嵌套过深的消息" }] };

  switch (content.type) {
    case "text":
    case "markdown": {
      const text = str(content.type === "text" ? content.text : content.markdown);
      return { parts: text ? [{ type: "text", text }] : [] };
    }

    case "attachment": {
      const mime = String(content.mimeType ?? "application/octet-stream").toLowerCase();
      const name = String(content.name ?? "attachment");
      const size = num(content.size);
      if (!mime.startsWith("image/")) {
        const textLimit = options.maxTextFileBytes ?? 0;
        if (options.saveTextFile && isTextLike(mime, name) && (size === undefined || size <= textLimit)) {
          // 文本类文件下载下来，由 Python 把内容交给 AI
          try {
            const saved = await options.saveTextFile(content);
            return { parts: [{ type: "text_file", path: saved.path, mime, name, size: saved.size }] };
          } catch (error) {
            options.warn(`文本文件下载失败（${name}）：${String((error as Error).message ?? error)}`);
            const code = (error as { code?: unknown }).code === "TOO_LARGE" ? "too_large" : "download_failed";
            const part: InboundPart = { type: "file", mime, name, note: code };
            if (size !== undefined) part.size = size;
            return { parts: [part] };
          }
        }
        // MaiBot 读不了 PDF、视频等文件，只告诉 AI 收到了什么
        const part: InboundPart = { type: "file", mime, name };
        if (size !== undefined) part.size = size;
        if (isTextLike(mime, name) && size !== undefined && size > textLimit) part.note = "text_too_large";
        return { parts: [part] };
      }
      if (size !== undefined && size > options.maxImageBytes) {
        return { parts: [{ type: "file", mime, name, size, note: "too_large" }] };
      }
      try {
        const saved = await options.saveImage(content);
        const part: InboundPart = { type: "image", path: saved.path, mime, name, size: saved.size };
        // 贴纸（包括第三方贴纸包、Memoji、照片抠出来的贴纸）在 iMessage 里都是带「贴纸」标记的图片
        if (options.stickerIds?.has(str(content.id) ?? "")) part.sticker = true;
        return { parts: [part] };
      } catch (error) {
        const code = (error as { code?: unknown }).code === "TOO_LARGE" ? "too_large" : "download_failed";
        options.warn(`图片附件下载失败（${name}，${mime}）：${String((error as Error).message ?? error)}`);
        const part: InboundPart = { type: "file", mime, name, note: code };
        if (size !== undefined) part.size = size;
        return { parts: [part] };
      }
    }

    case "voice": {
      const part: InboundPart = { type: "voice", mime: String(content.mimeType ?? "audio/x-caf") };
      const id = str(content.id);
      const duration = num(content.duration);
      const size = num(content.size);
      if (id) part.id = id;
      if (duration !== undefined) part.duration = duration;
      if (size !== undefined) part.size = size;
      if (options.saveVoice) {
        const saved = await downloadVoice(content, options);
        if (saved) part.path = saved;
        else part.note = "download_failed";
      }
      return { parts: [part] };
    }

    case "contact":
      return { parts: [contactPart(content)] };

    case "richlink": {
      const url = str(content.url);
      return { parts: url ? [{ type: "link", url }] : [{ type: "unsupported", what: "链接" }] };
    }

    case "app":
      return { parts: [await appPart(content)] };

    case "poll": {
      const options = Array.isArray(content.options)
        ? content.options.map((o) => str((o as { title?: unknown } | null)?.title)).filter((t): t is string => Boolean(t))
        : [];
      // Photon 送来的投票没有标题（见 polls.ts）
      const title = str(content.title);
      return { parts: [{ type: "poll", title: title && title !== UNTITLED_POLL ? title : "", options }] };
    }

    case "effect": {
      const inner = asContent(content.content);
      const converted = inner ? await convertContent(inner, options, depth + 1) : { parts: [] };
      const effect = str(content.effect);
      return { ...converted, parts: effect ? [...converted.parts, { type: "effect", effect }] : converted.parts };
    }

    case "group": {
      const parts: InboundPart[] = [];
      let replyTo: ReplyRef | undefined;
      const items = Array.isArray(content.items) ? content.items : [];
      for (const raw of items) {
        const item = asMessage(raw);
        if (!item) {
          parts.push({ type: "unsupported", what: "组合消息中的一项" });
          continue;
        }
        const converted = await convertContent(item.content, options, depth + 1);
        parts.push(...converted.parts);
        replyTo ??= converted.replyTo;
      }
      return replyTo ? { parts, replyTo } : { parts };
    }

    case "reply": {
      const inner = asContent(content.content);
      const converted = inner ? await convertContent(inner, options, depth + 1) : { parts: [] };
      const target = asMessage(content.target);
      if (!target) return converted;
      const replyTo: ReplyRef = { message_id: target.id };
      const text = previewText(target.content);
      if (text) replyTo.text = text;
      return { parts: converted.parts, replyTo };
    }

    case "custom": {
      const raw = content.raw as { imessage_type?: unknown } | undefined;
      const kind = str(raw?.imessage_type);
      return { parts: [{ type: "unsupported", what: kind === "unsupported-message" ? "暂不支持的消息" : (kind ?? "自定义内容") }] };
    }

    default:
      return { parts: [{ type: "unsupported", what: content.type }] };
  }
}

const VOICE_ATTEMPTS = 3;

function withTimeout<T>(work: Promise<T>, ms: number): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`超过 ${ms / 1000} 秒`)), ms);
  });
  return Promise.race([work, timeout]).finally(() => clearTimeout(timer));
}

/**
 * 下载语音。刚收到时附件可能还在从 Apple 传到服务器，下载会失败：等一会再试，最多 3 次，
 * 每次最多等 15 秒（下载期间后面的消息要排队）。都失败返回 undefined（调用方会让 AI 得体地
 * 告诉对方没听清）。超时放弃的那次如果之后才下载完，临时文件会在插件下次启动时清掉。
 */
async function downloadVoice(content: ContentLike, options: ConvertOptions): Promise<string | undefined> {
  const delay = options.voiceRetryDelayMs ?? 2000;
  const limit = options.voiceTimeoutMs ?? 15_000;
  for (let attempt = 1; attempt <= VOICE_ATTEMPTS; attempt++) {
    try {
      return (await withTimeout(options.saveVoice!(content), limit)).path;
    } catch (error) {
      const code = (error as { code?: unknown }).code;
      options.warn(`语音下载失败（第 ${attempt} 次）：${String((error as Error).message ?? error)}`);
      if (code === "TOO_LARGE") return undefined;
      if (attempt < VOICE_ATTEMPTS) await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  return undefined;
}

function controlEvent(content: ContentLike, eventId: string): ControlEvent {
  switch (content.type) {
    case "reaction": {
      const target = asMessage(content.target);
      const event: ControlEvent = { type: "reaction", emoji: String(content.emoji ?? ""), target_id: target?.id ?? "" };
      const text = previewText(target?.content);
      if (text) event.target_text = text;
      // 被点的是不是机器人自己发的消息
      if (target?.direction) event.target_from_me = target.direction === "outbound";
      return event;
    }
    case "read":
      return { type: "read", target_id: asMessage(content.target)?.id ?? "" };
    case "poll_option": {
      const poll = content.poll as { title?: unknown } | undefined;
      const option = content.option as { title?: unknown } | undefined;
      // Photon 送来的投票没有标题（见 polls.ts）：AI 发起的投票按编号补回标题，其他的留空
      const rawTitle = str(poll?.title);
      const title = rawTitle && rawTitle !== UNTITLED_POLL ? rawTitle : (sentPollTitle(eventId) ?? "");
      return {
        type: "poll_vote",
        poll_title: title,
        option: str(option?.title) ?? str(content.title) ?? "",
        selected: content.selected !== false,
      };
    }
    default:
      return { type: "other", content_type: content.type };
  }
}

/**
 * 对方编辑、撤回、取消点赞：SDK 本身不转发，打包补丁把它们转成 custom 内容（见 build-config.mjs）。
 * 不是这种内容时返回 undefined。
 */
function changeEvent(content: ContentLike, options: ConvertOptions): ControlEvent | undefined {
  if (content.type !== "custom") return undefined;
  const raw = content.raw as Record<string, unknown> | undefined;
  const kind = str(raw?.imessage_change);
  const guid = str(raw?.message_guid);
  if (!kind || !guid) return undefined;
  const partIndex = num(raw?.part_index);
  // 组合消息的某一部分，缓存里的编号是 p:<序号>/<编号>
  const original = (partIndex !== undefined ? options.lookupMessage?.(`p:${partIndex}/${guid}`) : undefined) ?? options.lookupMessage?.(guid);
  const oldText = previewText(original?.content);
  const fromMe = original?.direction ? original.direction === "outbound" : undefined;
  const extras = <T extends object>(event: T): T => {
    const out = { ...event } as T & { old_text?: string; target_from_me?: boolean };
    if (fromMe !== undefined) out.target_from_me = fromMe;
    return out;
  };
  if (kind === "message.edited") {
    const event: ControlEvent = extras({ type: "edited" as const, target_id: guid, text: str(raw?.text) ?? "" });
    if (oldText) event.old_text = oldText;
    return event;
  }
  if (kind === "message.unsent") {
    const event: ControlEvent = extras({ type: "unsent" as const, target_id: guid });
    if (oldText) event.old_text = oldText;
    return event;
  }
  if (kind === "message.reactionRemoved") {
    const event: ControlEvent = extras({ type: "reaction_removed" as const, emoji: String(raw?.reaction ?? ""), target_id: guid });
    if (oldText) event.target_text = oldText;
    return event;
  }
  return { type: "other", content_type: kind };
}

/**
 * 附加字段里标着贴纸的附件编号。spectrum-ts 给组合消息的每一项都复制了整条消息的附加字段，
 * 所以只看最外层就够了。
 */
function stickerIdsOf(message: MessageLike): Set<string> {
  const found = new Set<string>();
  for (const meta of message.attachmentMetadata ?? []) {
    if (meta.isSticker && meta.guid) found.add(meta.guid);
  }
  return found;
}

/**
 * 对方把贴纸贴在某条消息上（打包补丁转发的 message.stickerPlaced，见 build-config.mjs）。
 * 变成一条「引用那条消息 + 贴纸图片」的普通消息：贴纸是对方真正发出的东西，按对方的消息交给 MaiBot。
 */
async function placedSticker(
  message: MessageLike,
  options: ConvertOptions,
  meta: { chatId: string; senderId: string; timestamp: number },
): Promise<InboundResult | undefined> {
  if (message.content.type !== "custom") return undefined;
  const raw = message.content.raw as Record<string, unknown> | undefined;
  if (raw?.imessage_change !== "message.stickerPlaced") return undefined;
  const guid = str(raw.message_guid) ?? "";
  const partIndex = num(raw.part_index);
  const targetId = partIndex !== undefined ? `p:${partIndex}/${guid}` : guid;
  const sticker = asContent(raw.sticker);
  let part: InboundPart = { type: "sticker" };
  if (sticker) {
    const mime = String(sticker.mimeType ?? "image/png").toLowerCase();
    try {
      const saved = await options.saveImage(sticker);
      part = { type: "image", path: saved.path, mime, name: String(sticker.name ?? "sticker"), size: saved.size, sticker: true };
    } catch (error) {
      options.warn(`贴纸下载失败：${String((error as Error).message ?? error)}`);
    }
  }
  const original = options.lookupMessage?.(targetId) ?? options.lookupMessage?.(guid);
  const replyTo: ReplyRef = { message_id: original?.id ?? targetId };
  const text = previewText(original?.content);
  if (text) replyTo.text = text;
  return {
    kind: "message",
    data: {
      message_id: message.id,
      chat_id: meta.chatId,
      chat_type: message.space?.type ?? "dm",
      sender_id: meta.senderId,
      timestamp: meta.timestamp,
      parts: [part],
      reply_to: replyTo,
      // 编号是补丁拼出来的，Photon 不认识：不能被引用、点赞或标已读，要用被贴的那条消息
      quotable: false,
    },
  };
}

/** 消息正文（文字、引用回复里的文字）；没有原始正文时用它。 */
function plainText(content: ContentLike | undefined, depth = 0): string {
  if (!content || depth > MAX_DEPTH) return "";
  if (typeof content.text === "string") return content.text;
  if (content.type === "reply" || content.type === "effect") return plainText(asContent(content.content), depth + 1);
  return "";
}

/** iMessage 正文里图片、附件的占位符 */
const OBJECT_PLACEHOLDER = /\uFFFC/g;

/**
 * 对方用的特效：spectrum-ts 收消息时不会把特效包成 effect 内容，只把编号放在消息的附加字段上
 * （真机第三轮发现：之前 AI 根本看不到特效）。
 *
 * 只看最外层：组合消息的每一项都复制了整条消息的附加字段，逐项看会重复报好几次。
 * 文字效果的位置是按原始正文（nativeText，图片处有占位符）算的，所以按它来截取。
 */
function messageEffects(message: MessageLike): InboundPart[] {
  const found: InboundPart[] = [];
  const effect = str(message.expressiveSendStyleId);
  if (effect) found.push({ type: "effect", effect });
  const text = typeof message.nativeText === "string" ? message.nativeText : plainText(message.content);
  const seen = new Set<string>();
  for (const format of message.formatting ?? []) {
    const name = str(format.effectName);
    if (!name) continue; // 粗体、斜体等普通格式不用告诉 AI
    const piece = text.slice(format.start, format.start + format.length).replace(OBJECT_PLACEHOLDER, "").trim();
    const key = `${name}|${piece}`;
    if (seen.has(key)) continue;
    seen.add(key);
    found.push({ type: "text_effect", effect: name, text: piece });
  }
  return found;
}

/** 把一条 SDK 消息转换成发给 Python 的结果。 */
export async function convertInbound(message: MessageLike, options: ConvertOptions): Promise<InboundResult> {
  // 自己发出的消息回显不是用户的话，不转发
  if (message.direction === "outbound") return { kind: "echo" };

  const chatId = message.space?.id ?? "";
  const senderId = message.sender?.id ?? "";
  const timestamp = message.timestamp instanceof Date ? message.timestamp.getTime() : Date.now();

  const placed = await placedSticker(message, options, { chatId, senderId, timestamp });
  if (placed) return placed;

  const change = changeEvent(message.content, options);
  if (change) {
    return { kind: "control", data: { event_id: message.id, chat_id: chatId, sender_id: senderId, timestamp, event: change } };
  }

  if (CONTROL_TYPES.has(message.content.type)) {
    return {
      kind: "control",
      data: { event_id: message.id, chat_id: chatId, sender_id: senderId, timestamp, event: controlEvent(message.content, message.id) },
    };
  }

  const stickerIds = stickerIdsOf(message);
  const { parts, replyTo } = await convertContent(message.content, stickerIds.size ? { ...options, stickerIds } : options, 0);
  parts.push(...messageEffects(message));
  // 规则：绝不交出空消息，至少给一个占位
  const finalParts = parts.length ? parts : [{ type: "unsupported", what: "空白消息" } satisfies InboundPart];
  const data: InboundMessage = {
    message_id: message.id,
    chat_id: chatId,
    chat_type: message.space?.type ?? "dm",
    sender_id: senderId,
    timestamp,
    parts: finalParts,
  };
  if (replyTo) data.reply_to = replyTo;
  return { kind: "message", data };
}

/** 打包补丁转发来的变化（编辑、撤回、贴纸……）：编号是拼出来的，不能放进句柄缓存。 */
export function isForwardedChange(message: MessageLike): boolean {
  if (message.content?.type !== "custom") return false;
  return typeof (message.content.raw as { imessage_change?: unknown } | undefined)?.imessage_change === "string";
}

/** 收集一条消息里所有可被引用、点赞的子消息（组合消息的每一项也算）。 */
export function collectMessages(message: MessageLike, depth = 0): MessageLike[] {
  const found: MessageLike[] = [message];
  if (depth > MAX_DEPTH) return found;
  const content = message.content;
  const inner = content.type === "reply" ? asContent(content.content) : content;
  if (inner?.type === "group" && Array.isArray(inner.items)) {
    for (const raw of inner.items) {
      const item = asMessage(raw);
      if (item) found.push(...collectMessages(item, depth + 1));
    }
  }
  return found;
}
