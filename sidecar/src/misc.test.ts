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

/** 先给出 chunks，然后不再有数据也不结束（模拟卡住的下载）。 */
function stallingStream(chunks: Uint8Array[], openDelayMs = 0) {
  let cancelled = false;
  return {
    get cancelled() {
      return cancelled;
    },
    stream: async () => {
      if (openDelayMs) await new Promise((resolve) => setTimeout(resolve, openDelayMs));
      return new ReadableStream<Uint8Array>({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(chunk);
        },
        cancel() {
          cancelled = true;
        },
      });
    },
  };
}

test("附件下载卡住不动时放弃，并删掉半成品", async () => {
  const dir = await mkdtemp(join(tmpdir(), "imsg-media-"));
  // 一直等不到第一块数据
  const silent = stallingStream([]);
  await assert.rejects(saveToTempFile(silent, dir, ".jpg", 100, { firstChunkMs: 30, idleMs: 30 }), { code: "TIMEOUT" });
  assert.ok(silent.cancelled);
  // 传了一块就卡住
  await assert.rejects(saveToTempFile(stallingStream([new Uint8Array(4)]), dir, ".jpg", 100, { firstChunkMs: 30, idleMs: 30 }), { code: "TIMEOUT" });
  // 连流都打不开
  await assert.rejects(saveToTempFile(stallingStream([], 1000), dir, ".jpg", 100, { firstChunkMs: 30 }), { code: "TIMEOUT" });
  // 被外部取消
  const controller = new AbortController();
  const pending = saveToTempFile(stallingStream([new Uint8Array(4)]), dir, ".jpg", 100, { signal: controller.signal });
  setTimeout(() => controller.abort(), 20);
  await assert.rejects(pending, { code: "TIMEOUT" });
  assert.deepEqual(await readdir(dir), []);
});

test("附件下载慢但一直有进展时不会被打断", async () => {
  const dir = await mkdtemp(join(tmpdir(), "imsg-media-"));
  let sent = 0;
  const slow = {
    stream: async () =>
      new ReadableStream<Uint8Array>({
        async pull(controller) {
          await new Promise((resolve) => setTimeout(resolve, 20));
          if (sent++ < 6) controller.enqueue(new Uint8Array([sent]));
          else controller.close();
        },
      }),
  };
  // 总共约 140 毫秒，超过每一步的 50 毫秒上限，但每一步都没超
  const saved = await saveToTempFile(slow, dir, ".jpg", 100, { firstChunkMs: 50, idleMs: 50 });
  assert.equal(saved.size, 6);
});

test("SDK 日志整理成一行，并识别消息流中断与恢复", async () => {
  const { markLinkHealthy, resetLinkState, summarizeArgs, trackLinkState } = await import("./sdklog.ts");
  const error = Object.assign(new Error("No connection established.\nLast error: x"), { cause: new Error("ENETUNREACH 1.2.3.4:443") });
  const attrs = { "spectrum.stream.label": "imessage.messages:shared", "spectrum.stream.attempt": 2, "spectrum.stream.id": "abc" };
  const line = summarizeArgs(["[spectrum.stream]", "WARN", "stream interrupted; reconnecting", attrs, error]);
  assert.equal(line.includes("\n"), false);
  assert.match(line, /label=imessage.messages:shared, attempt=2/);
  assert.match(line, /Error: No connection established\.（原因：ENETUNREACH 1\.2\.3\.4:443）/);

  const events: object[] = [];
  const push = (data: object) => events.push(data);
  const states = () => events.map((e) => (e as { state: string }).state);
  const msgs = "imessage.messages:shared";
  const polls = "imessage.polls:shared";
  const down = (label: string) => ["[spectrum.stream]", "WARN", "stream interrupted; reconnecting", { "spectrum.stream.label": label }];
  const up = (label: string) => ["[spectrum.stream]", "INFO", "stream recovered", { "spectrum.stream.label": label }];

  resetLinkState();
  assert.equal(trackLinkState(down(msgs), push), "interrupted");
  assert.equal(trackLinkState(down(msgs), push), "repeat"); // 重试日志不再输出
  assert.equal(trackLinkState(down(polls), push), "interrupted");
  assert.deepEqual(states(), ["reconnecting"]);
  // 真机：投票流重连后一直没有事件，不会记 recovered；只要消息流恢复，就算恢复
  assert.equal(trackLinkState(up(msgs), push), "recovered");
  assert.deepEqual(states(), ["reconnecting", "ok"]);
  // 投票流的中断记录也一起清掉，下次断网时照常发 reconnecting
  assert.equal(trackLinkState(down(polls), push), "interrupted");
  assert.deepEqual(states(), ["reconnecting", "ok"]); // 只有投票流断：不算连接中断
  assert.equal(trackLinkState(down(msgs), push), "interrupted");
  assert.deepEqual(states(), ["reconnecting", "ok", "reconnecting"]);
  // 收到新消息也说明连接正常
  markLinkHealthy(push);
  markLinkHealthy(push);
  assert.deepEqual(states(), ["reconnecting", "ok", "reconnecting", "ok"]);
  assert.equal(trackLinkState(down(msgs), push), "interrupted");
  resetLinkState();
});
