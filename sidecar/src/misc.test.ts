import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { LruCache } from "./handles.ts";
import { safeExtension, saveToTempFile } from "./media.ts";
import { classifyError, parseRequest } from "./protocol.ts";

test("LRU 缓存淘汰最久没用的", () => {
  const cache = new LruCache<number>(2);
  cache.set("a", 1);
  cache.set("b", 2);
  cache.get("a");
  cache.set("c", 3);
  assert.equal(cache.has("b"), false);
  assert.equal(cache.get("a"), 1);
  assert.equal(cache.get("c"), 3);
});

test("请求解析", () => {
  assert.deepEqual(parseRequest('{"id":1,"op":"ping"}'), { id: 1, op: "ping", args: {} });
  assert.throws(() => parseRequest("not json"), { code: "BAD_REQUEST" });
  assert.throws(() => parseRequest('{"op":"ping"}'), { code: "BAD_REQUEST" });
  assert.throws(() => parseRequest('{"id":1,"op":"x","args":[1]}'), { code: "BAD_REQUEST" });
});

test("错误归类", () => {
  const named = (name: string, message: string, extra: object = {}) => Object.assign(new Error(message), { name }, extra);
  assert.equal(classifyError(named("AuthenticationError", "[spectrum-imessage] Target not allowed for this project")).code, "TARGET_NOT_ALLOWED");
  assert.equal(classifyError(named("AuthenticationError", "bad token")).code, "AUTH");
  assert.equal(classifyError(named("SpectrumCloudError", "no", { status: 401 })).code, "AUTH");
  assert.equal(classifyError(named("SpectrumCloudError", "no", { status: 503 })).code, "CLOUD_503");
  assert.equal(classifyError(Object.assign(new TypeError("fetch failed"), { cause: { code: "ENOTFOUND" } })).code, "NETWORK");
  assert.equal(classifyError("weird").code, "INTERNAL");
});

test("临时文件扩展名", () => {
  assert.equal(safeExtension("IMG_1.HEIC", "image/heic"), ".heic");
  assert.equal(safeExtension("weird name", "image/jpeg"), ".jpg");
  assert.equal(safeExtension("", "application/x-unknown"), ".bin");
});

function streamOf(chunks: Uint8Array[]) {
  return {
    stream: async () =>
      new ReadableStream<Uint8Array>({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(chunk);
          controller.close();
        },
      }),
  };
}

test("附件写入临时文件，超限时中止并清理", async () => {
  const dir = await mkdtemp(join(tmpdir(), "imsg-media-"));
  const saved = await saveToTempFile(streamOf([new Uint8Array([1, 2]), new Uint8Array([3])]), dir, ".jpg", 10);
  assert.equal(saved.size, 3);
  assert.deepEqual([...(await readFile(saved.path))], [1, 2, 3]);

  await assert.rejects(saveToTempFile(streamOf([new Uint8Array(8), new Uint8Array(8)]), dir, ".jpg", 10), { code: "TOO_LARGE" });
  assert.deepEqual(await readdir(dir), [saved.path.split("/").pop()]);
});

test("SDK 日志整理成一行，并识别消息流中断与恢复", async () => {
  const { summarizeArgs, trackLinkState } = await import("./sdklog.ts");
  const error = Object.assign(new Error("No connection established.\nLast error: x"), { cause: new Error("ENETUNREACH 1.2.3.4:443") });
  const attrs = { "spectrum.stream.label": "imessage.messages:shared", "spectrum.stream.attempt": 2, "spectrum.stream.id": "abc" };
  const line = summarizeArgs(["[spectrum.stream]", "WARN", "stream interrupted; reconnecting", attrs, error]);
  assert.equal(line.includes("\n"), false);
  assert.match(line, /label=imessage.messages:shared, attempt=2/);
  assert.match(line, /Error: No connection established\.（原因：ENETUNREACH 1\.2\.3\.4:443）/);

  const events: object[] = [];
  const push = (data: object) => events.push(data);
  const down = (label: string) => ["[spectrum.stream]", "WARN", "stream interrupted; reconnecting", { "spectrum.stream.label": label }];
  const up = (label: string) => ["[spectrum.stream]", "INFO", "stream recovered", { "spectrum.stream.label": label }];
  assert.equal(trackLinkState(down("a"), push), "interrupted");
  assert.equal(trackLinkState(down("a"), push), "repeat"); // 重试日志不再输出
  assert.equal(trackLinkState(down("b"), push), "interrupted");
  assert.equal(trackLinkState(up("a"), push), "recovered");
  assert.equal(events.length, 1); // b 还没恢复
  trackLinkState(up("b"), push);
  assert.deepEqual(events.map((e) => (e as { state: string }).state), ["reconnecting", "ok"]);
});
