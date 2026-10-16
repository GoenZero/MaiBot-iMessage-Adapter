/**
 * 附件临时文件。
 *
 * 入站附件下载后写进 MEDIA_DIR，管道里只传路径，由 Python 读取后删除；
 * 出站附件由 Python 写进 MEDIA_DIR，侧车读取发送，Python 在收到回执后删除。
 * 读写都有大小上限，超限时明确报错，不会把超大数据塞进内存或管道。
 */
import { randomBytes } from "node:crypto";
import { open, realpath, rm } from "node:fs/promises";
import { extname, isAbsolute, join, relative, sep } from "node:path";

import { BridgeError } from "./protocol.ts";

/** 可以流式读取字节的对象（SDK 的附件和语音都满足）。 */
export interface StreamSource {
  stream(): Promise<ReadableStream<unknown>>;
}

const EXTENSION_BY_MIME: Record<string, string> = {
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/gif": ".gif",
  "image/webp": ".webp",
  "image/heic": ".heic",
  "image/heif": ".heif",
  "audio/x-caf": ".caf",
  "audio/mp4": ".m4a",
  "audio/aac": ".aac",
  "application/pdf": ".pdf",
  "text/vcard": ".vcf",
};

/** 根据文件名或 MIME 选一个安全的扩展名（只用于临时文件名）。 */
export function safeExtension(name: string, mimeType: string): string {
  const fromName = extname(name).toLowerCase();
  if (/^\.[a-z0-9]{1,8}$/.test(fromName)) return fromName;
  return EXTENSION_BY_MIME[mimeType.toLowerCase()] ?? ".bin";
}

export interface SaveOptions {
  /** 外部取消（例如语音下载超时）：取消后中止下载、删除半成品 */
  signal?: AbortSignal;
  /** 等第一块数据最多多久（毫秒）。Photon 有时要先从 Apple 取完附件才开始传，所以要给得宽一些 */
  firstChunkMs?: number;
  /** 之后两块数据之间最多隔多久（毫秒）。只要一直有数据进来，大文件下载多久都行 */
  idleMs?: number;
}

/** 等一个异步步骤，最多等 ms 毫秒；外部取消时立刻放弃。都不设时原样等待。 */
export async function guarded<T>(work: Promise<T>, ms: number | undefined, signal: AbortSignal | undefined, what: string): Promise<T> {
  if (!ms && !signal) return work;
  let timer: NodeJS.Timeout | undefined;
  let onAbort: (() => void) | undefined;
  const stop = new Promise<never>((_, reject) => {
    if (ms) timer = setTimeout(() => reject(new BridgeError("TIMEOUT", `${what}超过 ${Math.round(ms / 1000)} 秒没有进展`)), ms);
    if (signal) {
      onAbort = () => reject(new BridgeError("TIMEOUT", `${what}已取消`));
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }
  });
  try {
    return await Promise.race([work, stop]);
  } finally {
    clearTimeout(timer);
    if (signal && onAbort) signal.removeEventListener("abort", onAbort);
  }
}

/**
 * 把附件流式写进临时文件，超过 maxBytes 立即中止并删除半成品。
 *
 * 下载卡住（长时间没有新数据）或被取消时也中止并删除半成品：收消息是一条一条处理的，
 * 一个卡住的下载会让后面所有消息都收不到（外部审查发现）。
 *
 * 返回写入的文件路径和实际字节数。
 */
export async function saveToTempFile(
  source: StreamSource,
  mediaDir: string,
  extension: string,
  maxBytes: number,
  options: SaveOptions = {},
): Promise<{ path: string; size: number }> {
  const path = join(mediaDir, `in-${Date.now()}-${randomBytes(6).toString("hex")}${extension}`);
  const handle = await open(path, "wx", 0o600);
  let size = 0;
  let completed = false;
  try {
    const opening = source.stream();
    let stream: Awaited<ReturnType<StreamSource["stream"]>>;
    try {
      stream = await guarded(opening, options.firstChunkMs, options.signal, "附件下载");
    } catch (error) {
      // 放弃等待后流才打开的话，把它关掉，不留着占连接
      void opening.then((late) => late.cancel()).catch(() => undefined);
      throw error;
    }
    const reader = stream.getReader();
    try {
      let first = true;
      while (true) {
        const { done, value } = await guarded(reader.read(), first ? options.firstChunkMs : options.idleMs, options.signal, "附件下载");
        first = false;
        if (done) break;
        const chunk = value instanceof Uint8Array ? value : new Uint8Array(value as ArrayBuffer);
        size += chunk.byteLength;
        if (size > maxBytes) throw new BridgeError("TOO_LARGE", `附件超过上限 ${maxBytes} 字节`);
        await handle.write(chunk);
      }
    } finally {
      // 不等取消完成：卡住的流取消也可能卡住
      void reader.cancel().catch(() => undefined);
    }
    completed = true;
    return { path, size };
  } finally {
    await handle.close();
    if (!completed) await rm(path, { force: true });
  }
}

/**
 * 读取 Python 放进 MEDIA_DIR 的出站附件。
 *
 * 只接受 MEDIA_DIR 里面的普通文件，并限制大小，防止被诱导去读任意路径。
 */
export async function readOutboundFile(path: string, mediaDir: string, maxBytes: number): Promise<Buffer> {
  let resolved: string;
  let root: string;
  try {
    [resolved, root] = await Promise.all([realpath(path), realpath(mediaDir)]);
  } catch {
    throw new BridgeError("NOT_FOUND", "出站附件文件不存在");
  }
  const delta = relative(root, resolved);
  if (!delta || isAbsolute(delta) || delta === ".." || delta.startsWith(`..${sep}`)) {
    throw new BridgeError("BAD_REQUEST", "出站附件必须位于临时目录内");
  }
  const handle = await open(resolved, "r");
  try {
    const info = await handle.stat();
    if (!info.isFile()) throw new BridgeError("BAD_REQUEST", "出站附件不是普通文件");
    if (info.size > maxBytes) throw new BridgeError("TOO_LARGE", `出站附件超过上限 ${maxBytes} 字节`);
    return await handle.readFile();
  } finally {
    await handle.close();
  }
}
