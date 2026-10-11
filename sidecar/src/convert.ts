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
}

export type InboundPart =
  | { type: "text"; text: string }
  | { type: "image"; path: string; mime: string; name: string; size: number }
  | { type: "file"; mime: string; name: string; size?: number; note?: string }
  | { type: "text_file"; path: string; mime: string; name: string; size: number }
  | { type: "voice"; id?: string; mime: string; duration?: number; size?: number }
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
  reply_to?: ReplyRef;
}

export type ControlEvent =
  | { type: "reaction"; emoji: string; target_id: string; target_text?: string; target_from_me?: boolean }
  | { type: "read"; target_id: string }
  | { type: "poll_vote"; poll_title: string; option: string; selected: boolean }
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
        return { parts: [{ type: "image", path: saved.path, mime, name, size: saved.size }] };
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
      return { parts: [{ type: "poll", title: str(content.title) ?? "", options }] };
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

function controlEvent(content: ContentLike): ControlEvent {
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
      return {
        type: "poll_vote",
        poll_title: str(poll?.title) ?? "",
        option: str(option?.title) ?? str(content.title) ?? "",
        selected: content.selected !== false,
      };
    }
    default:
      return { type: "other", content_type: content.type };
  }
}

/** 把一条 SDK 消息转换成发给 Python 的结果。 */
export async function convertInbound(message: MessageLike, options: ConvertOptions): Promise<InboundResult> {
  // 自己发出的消息回显不是用户的话，不转发
  if (message.direction === "outbound") return { kind: "echo" };

  const chatId = message.space?.id ?? "";
  const senderId = message.sender?.id ?? "";
  const timestamp = message.timestamp instanceof Date ? message.timestamp.getTime() : Date.now();

  if (CONTROL_TYPES.has(message.content.type)) {
    return {
      kind: "control",
      data: { event_id: message.id, chat_id: chatId, sender_id: senderId, timestamp, event: controlEvent(message.content) },
    };
  }

  const { parts, replyTo } = await convertContent(message.content, options, 0);
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
