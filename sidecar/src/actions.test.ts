import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { type ActionMessage, Actions, type Builders, type SpaceLike } from "./actions.ts";
import { LruCache } from "./handles.ts";

const builders: Builders = {
  text: (value) => ({ kind: "text", value }),
  markdown: (value) => ({ kind: "markdown", value }),
  attachment: (data, options) => ({ kind: "attachment", bytes: data.length, ...options }),
};

function sentMessage(id: string): ActionMessage {
  return { id, content: { type: "text", text: "" }, direction: "outbound" };
}

function setup(sendResult: (contents: unknown[]) => unknown = (c) => (c.length === 1 ? sentMessage("out-1") : c.map((_, i) => sentMessage(`out-${i + 1}`)))) {
  const calls: { op: string; contents?: unknown[] }[] = [];
  const handles = new LruCache<ActionMessage>(10);
  const space: SpaceLike = {
    id: "any;-;+1",
    send: async (...contents) => {
      calls.push({ op: "send", contents });
      return sendResult(contents);
    },
    startTyping: async () => void calls.push({ op: "startTyping" }),
    stopTyping: async () => void calls.push({ op: "stopTyping" }),
    getMessage: async () => undefined,
  };
  const warnings: string[] = [];
  const actions = new Actions({
    resolveSpace: async () => space,
    handles,
    mediaDir: tmpdir(),
    maxAttachmentBytes: 1024,
    builders,
    warn: (m) => warnings.push(m),
  });
  return { actions, calls, handles, warnings, space };
}

test("发送文字，回报 iMessage 编号并存入句柄缓存", async () => {
  const { actions, calls, handles } = setup();
  const result = await actions.send({ chat_id: "+1", parts: [{ type: "text", text: "你好" }] });
  assert.deepEqual(result, { message_ids: ["out-1"], quoted: false, partial: false });
  assert.deepEqual(calls[0]?.contents, [{ kind: "text", value: "你好" }]);
  assert.ok(handles.get("out-1"));
});

test("markdown 标记的文字用 markdown 构造", async () => {
  const { actions, calls } = setup();
  await actions.send({ chat_id: "+1", parts: [{ type: "text", text: "**粗体**", markdown: true }] });
  assert.deepEqual(calls[0]?.contents, [{ kind: "markdown", value: "**粗体**" }]);
});

test("SDK 返回空结果算失败（内容被跳过）", async () => {
  const { actions } = setup(() => undefined);
  await assert.rejects(actions.send({ chat_id: "+1", parts: [{ type: "text", text: "x" }] }), { code: "NOT_DELIVERED" });
});

test("多段内容只送达一部分时标记 partial", async () => {
  const { actions } = setup(() => [sentMessage("out-1")]);
  const result = await actions.send({ chat_id: "+1", parts: [{ type: "text", text: "a" }, { type: "text", text: "b" }] });
  assert.equal(result.partial, true);
});

test("引用回复：找到目标就用 reply，找不到就普通发送并警告", async () => {
  const { actions, handles, warnings, calls } = setup();
  const replied: unknown[][] = [];
  handles.set("in-1", {
    id: "in-1",
    content: { type: "text", text: "问题" },
    reply: async (...contents) => {
      replied.push(contents);
      return sentMessage("out-r");
    },
  });
  const quoted = await actions.send({ chat_id: "+1", parts: [{ type: "text", text: "答" }], reply_to: "in-1" });
  assert.deepEqual(quoted, { message_ids: ["out-r"], quoted: true, partial: false });
  assert.equal(replied.length, 1);

  const fallback = await actions.send({ chat_id: "+1", parts: [{ type: "text", text: "答" }], reply_to: "gone" });
  assert.equal(fallback.quoted, false);
  assert.equal(calls.length, 1);
  assert.equal(warnings.length, 1);
});

test("出站附件只能来自临时目录，且受大小限制", async () => {
  const dir = await mkdtemp(join(tmpdir(), "imsg-test-"));
  const small = join(dir, "a.png");
  await writeFile(small, Buffer.alloc(10));
  const { actions, calls } = setup();
  // mediaDir 是 tmpdir()，dir 在其下
  await actions.send({ chat_id: "+1", parts: [{ type: "file", path: small, mime: "image/png" }] });
  assert.deepEqual(calls[0]?.contents, [{ kind: "attachment", bytes: 10, mimeType: "image/png" }]);

  const big = join(dir, "big.png");
  await writeFile(big, Buffer.alloc(2048));
  await assert.rejects(actions.send({ chat_id: "+1", parts: [{ type: "file", path: big, mime: "image/png" }] }), { code: "TOO_LARGE" });
  await assert.rejects(
    actions.send({ chat_id: "+1", parts: [{ type: "file", path: "/etc/hostname", mime: "text/plain" }] }),
    (error: { code?: string }) => error.code === "BAD_REQUEST" || error.code === "NOT_FOUND",
  );
});

test("点赞：六种类型，找不到消息时明确报错", async () => {
  const { actions, handles } = setup();
  const reacted: string[] = [];
  handles.set("in-2", {
    id: "in-2",
    content: { type: "text", text: "谢谢" },
    react: async (emoji) => {
      reacted.push(emoji);
      return { id: "rx-1", content: { type: "reaction" } };
    },
  });
  assert.deepEqual(await actions.react({ chat_id: "+1", message_id: "in-2", kind: "love" }), { reaction_id: "rx-1" });
  assert.deepEqual(reacted, ["❤️"]);
  await assert.rejects(actions.react({ chat_id: "+1", message_id: "in-2", kind: "angry" }), { code: "BAD_REQUEST" });
  await assert.rejects(actions.react({ chat_id: "+1", message_id: "nope", kind: "like" }), { code: "MESSAGE_NOT_FOUND" });
});

test("输入中开始与停止", async () => {
  const { actions, calls } = setup();
  await actions.typing({ chat_id: "+1", state: "start" });
  await actions.typing({ chat_id: "+1", state: "stop" });
  assert.deepEqual(calls.map((c) => c.op), ["startTyping", "stopTyping"]);
  await assert.rejects(actions.typing({ chat_id: "+1", state: "maybe" }), { code: "BAD_REQUEST" });
});
