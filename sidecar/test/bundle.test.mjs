// 打包回归测试：打包后的代码放到一个旁边没有 node_modules 的目录里（和设备上一样），
// 确认 gRPC 客户端能创建出来。对应 0.2.0 首次真机部署时
// 「createGrpcClient needs the optional peer dependencies」导致连不上 Photon 的问题。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

import { buildOptions } from "../build-config.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const ENTRY = `
import { createGrpcClient } from "@photon-ai/advanced-imessage";
try {
  const client = createGrpcClient({ address: "127.0.0.1:9", token: "test" });
  await Promise.resolve(client);
  console.log("GRPC_CLIENT_OK");
  process.exit(0);
} catch (error) {
  console.log("GRPC_CLIENT_FAILED " + error.message.split("\\n")[0] + " | " + String(error.cause));
  process.exit(1);
}
`;

async function bundleAndRun(usePatch) {
  // 入口放在 sidecar/ 下面才能解析到 node_modules；产物放到系统临时目录
  const entryDir = join(here, ".tmp-bundle-test");
  mkdirSync(entryDir, { recursive: true });
  const entry = join(entryDir, "entry.mjs");
  writeFileSync(entry, ENTRY);
  const outDir = mkdtempSync(join(tmpdir(), "sidecar-bundle-test-"));
  const outfile = join(outDir, "bundle.mjs");
  try {
    const stats = { patched: 0 };
    const options = buildOptions({ entryPoints: [entry], outfile, stats });
    if (!usePatch) options.plugins = [];
    await build({ ...options, metafile: false });
    if (usePatch) assert.equal(stats.patched, 1);
    try {
      return execFileSync(process.execPath, [outfile], { encoding: "utf8", timeout: 30_000 });
    } catch (error) {
      return String(error.stdout ?? "") + String(error.stderr ?? "");
    }
  } finally {
    rmSync(entryDir, { recursive: true, force: true });
    rmSync(outDir, { recursive: true, force: true });
  }
}

test("不修补时能复现问题（证明测试有效）", async () => {
  const output = await bundleAndRun(false);
  assert.match(output, /GRPC_CLIENT_FAILED .*optional peer dependencies/);
});

test("修补后打包的 gRPC 客户端可以创建", async () => {
  const output = await bundleAndRun(true);
  assert.match(output, /GRPC_CLIENT_OK/, output);
});

test("打包产物里，投票的空标题会换成占位标题（否则对方的投票全部被丢掉）", async () => {
  const { UNTITLED_POLL } = await import("../build-config.mjs");
  const polls = await import("../src/polls.ts");
  assert.equal(polls.UNTITLED_POLL, UNTITLED_POLL); // 打包补丁和侧车用的是同一个占位标题
  const bundled = readFileSync(join(here, "..", "dist", "sidecar.mjs"), "utf8");
  // 打包时变量名会被改（例如 input3），只认结构
  assert.match(bundled, new RegExp(`toCachedPoll=.{0,60}title:\\w+\\.title\\|\\|${JSON.stringify(UNTITLED_POLL)}`));
});

test("修补后，对方的编辑、撤回、取消点赞、贴纸、发起的投票会被转发（机器人自己的不转发）", async () => {
  const { patchImessageSource } = await import("../build-config.mjs");
  const dist = join(here, "..", "node_modules", "@spectrum-ts", "imessage", "dist");
  const patchedFile = join(dist, `.patched-test-${process.pid}.js`);
  const source = readFileSync(join(dist, "index.js"), "utf8");
  // 放在原文件旁边，相对导入才能解析；末尾导出内部函数供测试调用
  writeFileSync(
    patchedFile,
    `${patchImessageSource(source)}\nexport { toMessageItem as __toMessageItem, toPollItem as __toPollItem, messages$1 as __messages };\n`,
  );
  try {
    const { __toMessageItem: toMessageItem } = await import(patchedFile);
    const base = { chatGuid: "any;-;+15550001111", isFromMe: false, occurredAt: new Date(0), sequence: 7, messageGuid: "GUID-1" };
    const edited = await toMessageItem({}, { ...base, type: "message.edited", content: { text: "改后的话" } }, "shared", "c1");
    assert.equal(edited.values.length, 1);
    const value = edited.values[0];
    assert.equal(value.sender.id, "+15550001111");
    assert.equal(value.space.id, "any;-;+15550001111");
    assert.deepEqual(value.content, {
      type: "custom",
      raw: { imessage_change: "message.edited", message_guid: "GUID-1", text: "改后的话", reaction: undefined, part_index: undefined, sticker: undefined },
    });
    const unsent = await toMessageItem({}, { ...base, type: "message.unsent" }, "shared", "c2");
    assert.equal(unsent.values[0].content.raw.imessage_change, "message.unsent");
    const removed = await toMessageItem({}, { ...base, type: "message.reactionRemoved", reaction: { kind: "love" } }, "shared", "c3");
    assert.equal(removed.values[0].content.raw.reaction, "❤️");
    const placed = await toMessageItem(
      {},
      { ...base, type: "message.stickerPlaced", sticker: { guid: "ATT-9", fileName: "cat.png", mimeType: "image/png", totalBytes: 10 }, targetPartIndex: 0 },
      "shared",
      "c5",
    );
    const raw = placed.values[0].content.raw;
    assert.equal(raw.imessage_change, "message.stickerPlaced");
    assert.equal(raw.part_index, 0);
    assert.equal(raw.sticker.type, "attachment");
    assert.equal(raw.sticker.id, "ATT-9");
    assert.equal(typeof raw.sticker.read, "function"); // 贴纸图片可以下载
    const mine = await toMessageItem({}, { ...base, isFromMe: true, type: "message.unsent" }, "shared", "c4");
    assert.deepEqual(mine.values, []);

    // 对方发起投票（Photon 送来的标题是空的），随后自己投了一票
    const { __toPollItem: toPollItem } = await import(patchedFile);
    const { UNTITLED_POLL } = await import("../build-config.mjs");
    const cache = new Map();
    const poll = { type: "poll.changed", pollMessageGuid: "POLL-1", chatGuid: base.chatGuid, isFromMe: false, occurredAt: new Date(0), actor: { address: "+15550001111" } };
    const options = [{ optionIdentifier: "a", text: "海边" }, { optionIdentifier: "b", text: "爬山" }];
    const created = await toPollItem({}, cache, { ...poll, sequence: 1, delta: { type: "created", title: "", options } }, "shared", "p1");
    assert.equal(created.values.length, 1);
    assert.equal(created.values[0].id, "POLL-1");
    assert.deepEqual(created.values[0].content, { type: "poll", title: UNTITLED_POLL, options: [{ title: "海边" }, { title: "爬山" }] });
    const voted = await toPollItem({}, cache, { ...poll, sequence: 2, delta: { type: "voted", optionIdentifier: "b" } }, "shared", "p2");
    assert.equal(voted.values[0].content.type, "poll_option");
    assert.equal(voted.values[0].content.option.title, "爬山");
    // 贴贴纸：消息流启动时登记入口；先上传图片，再贴到消息（组合消息的某一部分要带 partIndex）
    const { __messages: startMessages } = await import(patchedFile);
    const calls = [];
    const remote = {
      attachments: { upload: async (input) => (calls.push(["upload", input.fileName, input.data.length]), { attachment: { guid: "UP-1" } }) },
      messages: { placeSticker: async (...args) => (calls.push(["place", ...args]), { guid: "ST-1" }) },
    };
    delete globalThis.__imessagePlaceSticker;
    startMessages([{ client: remote, phone: "shared" }], undefined, undefined);
    assert.equal(typeof globalThis.__imessagePlaceSticker, "function");
    const result = await globalThis.__imessagePlaceSticker({
      spaceId: "any;-;+1",
      phone: "shared",
      messageId: "p:1/MSG-1",
      data: new Uint8Array([1, 2, 3]),
      fileName: "s.png",
      placement: { x: 0.5, y: 0.5 },
    });
    assert.deepEqual(result, { id: "ST-1" });
    assert.deepEqual(calls, [
      ["upload", "s.png", 3],
      ["place", "any;-;+1", "MSG-1", "UP-1", { x: 0.5, y: 0.5 }, { partIndex: 1 }],
    ]);
    delete globalThis.__imessagePlaceSticker;

    // 机器人自己发起的投票不当成对方的消息
    const ownPoll = await toPollItem({}, new Map(), { ...poll, isFromMe: true, sequence: 3, delta: { type: "created", title: "", options } }, "shared", "p3");
    assert.deepEqual(ownPoll.values, []);
  } finally {
    rmSync(patchedFile, { force: true });
  }
});
