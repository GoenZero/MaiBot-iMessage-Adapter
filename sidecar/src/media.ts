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

/**
 * 把附件流式写进临时文件，超过 maxBytes 立即中止并删除半成品。
 *
 * 返回写入的文件路径和实际字节数。
 */
export async function saveToTempFile(
  source: StreamSource,
  mediaDir: string,
  extension: string,
  maxBytes: number,
): Promise<{ path: string; size: number }> {
  const path = join(mediaDir, `in-${Date.now()}-${randomBytes(6).toString("hex")}${extension}`);
  const handle = await open(path, "wx", 0o600);
  let size = 0;
  let completed = false;
  try {
    const reader = (await source.stream()).getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = value instanceof Uint8Array ? value : new Uint8Array(value as ArrayBuffer);
        size += chunk.byteLength;
        if (size > maxBytes) throw new BridgeError("TOO_LARGE", `附件超过上限 ${maxBytes} 字节`);
        await handle.write(chunk);
      }
    } finally {
      await reader.cancel().catch(() => undefined);
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
