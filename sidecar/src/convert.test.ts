import assert from "node:assert/strict";
import { test } from "node:test";

import { collectMessages, type ContentLike, type ConvertOptions, convertInbound, type MessageLike } from "./convert.ts";

const space = { id: "any;-;+8613800000000", type: "dm" };
const sender = { id: "+8613800000000" };

function msg(id: string, content: ContentLike, extra: Partial<MessageLike> = {}): MessageLike {
  return { id, content, sender, space, direction: "inbound", timestamp: new Date(1700000000000), ...extra };
}

function options(overrides: Partial<ConvertOptions> = {}): ConvertOptions {
  return {
    maxImageBytes: 10 * 1024 * 1024,
    warn: () => undefined,
    saveImage: async (content) => ({ path: `/tmp/${String(content.name)}`, size: 1234 }),
    ...overrides,
  };
}

const image = (name: string, mimeType = "image/jpeg", size?: number): ContentLike => ({
  type: "attachment",
  id: `att-${name}`,
  name,
  mimeType,
  ...(size === undefined ? {} : { size }),
});

test("纯文字", async () => {
  const result = await convertInbound(msg("m1", { type: "text", text: "你好" }), options());
  assert.equal(result.kind, "message");
  assert.deepEqual(result.kind === "message" && result.data, {
    message_id: "m1",
    chat_id: space.id,
    chat_type: "dm",
    sender_id: sender.id,
    timestamp: 1700000000000,
    parts: [{ type: "text", text: "你好" }],
  });
});

test("HEIC 照片不会被拦，作为图片交出", async () => {
  const result = await convertInbound(msg("m2", image("IMG_0001.HEIC", "image/heic")), options());
  assert.ok(result.kind === "message");
  assert.deepEqual(result.data.parts, [
    { type: "image", path: "/tmp/IMG_0001.HEIC", mime: "image/heic", name: "IMG_0001.HEIC", size: 1234 },
  ]);
});

test("文字加两张照片的组合消息，三部分都在", async () => {
  const group: ContentLike = {
    type: "group",
    items: [
      msg("p:0/m3", { type: "text", text: "看看这两张" }),
      msg("p:1/m3", image("a.jpg")),
      msg("p:2/m3", image("b.png", "image/png")),
    ],
  };
  const result = await convertInbound(msg("m3", group), options());
  assert.ok(result.kind === "message");
  assert.equal(result.data.message_id, "m3");
  assert.deepEqual(
    result.data.parts.map((p) => p.type),
    ["text", "image", "image"],
  );
});

test("引用回复：正文保留，并带上被引用消息的编号和预览", async () => {
  const target = msg("bot-1", { type: "text", text: "明天下雨吗" }, { direction: "outbound" });
  const result = await convertInbound(
    msg("m4", { type: "reply", content: { type: "text", text: "会的" }, target }),
    options(),
  );
  assert.ok(result.kind === "message");
  assert.deepEqual(result.data.parts, [{ type: "text", text: "会的" }]);
  assert.deepEqual(result.data.reply_to, { message_id: "bot-1", text: "明天下雨吗" });
});

test("引用回复里套组合消息", async () => {
  const target = msg("bot-2", { type: "text", text: "发张照片" });
  const inner: ContentLike = { type: "group", items: [msg("p:0/m5", image("c.jpg")), msg("p:1/m5", { type: "text", text: "给" })] };
  const result = await convertInbound(msg("m5", { type: "reply", content: inner, target }), options());
  assert.ok(result.kind === "message");
  assert.deepEqual(result.data.parts.map((p) => p.type), ["image", "text"]);
  assert.equal(result.data.reply_to?.message_id, "bot-2");
});

test("名片转成姓名、电话、邮箱", async () => {
  const contact: ContentLike = {
    type: "contact",
    name: { formatted: "张三" },
    phones: [{ value: "+86 138 0000 0000", type: "mobile" }],
    emails: [{ value: "zhang@example.com" }],
    org: { name: "某公司" },
  };
  const result = await convertInbound(msg("m6", contact), options());
  assert.ok(result.kind === "message");
  assert.deepEqual(result.data.parts, [
    { type: "contact", name: "张三", phones: ["+86 138 0000 0000"], emails: ["zhang@example.com"], urls: [], org: "某公司" },
  ]);
});

test("PDF 不再被当成图片", async () => {
  let downloaded = false;
  const result = await convertInbound(
    msg("m7", image("合同.pdf", "application/pdf", 2048)),
    options({
      saveImage: async () => {
        downloaded = true;
        return { path: "/x", size: 1 };
      },
    }),
  );
  assert.ok(result.kind === "message");
  assert.deepEqual(result.data.parts, [{ type: "file", mime: "application/pdf", name: "合同.pdf", size: 2048 }]);
  assert.equal(downloaded, false);
});

test("超大图片不下载，改成文件说明", async () => {
  const result = await convertInbound(msg("m8", image("big.jpg", "image/jpeg", 99 * 1024 * 1024)), options());
  assert.ok(result.kind === "message");
  assert.equal(result.data.parts[0]?.type, "file");
  assert.equal(result.data.parts[0]?.type === "file" && result.data.parts[0].note, "too_large");
});

test("图片下载失败时也不丢消息", async () => {
  const warnings: string[] = [];
  const result = await convertInbound(
    msg("m9", image("x.jpg")),
    options({
      warn: (m) => warnings.push(m),
      saveImage: async () => {
        throw new Error("UNAVAILABLE");
      },
    }),
  );
  assert.ok(result.kind === "message");
  assert.equal(result.data.parts[0]?.type === "file" && result.data.parts[0].note, "download_failed");
  assert.equal(warnings.length, 1);
});

test("语音、链接、投票、卡片都有去处", async () => {
  const cases: [ContentLike, string][] = [
    [{ type: "voice", id: "v1", mimeType: "audio/x-caf", duration: 3 }, "voice"],
    [{ type: "richlink", url: "https://example.com" }, "link"],
    [{ type: "poll", title: "吃什么", options: [{ title: "面" }, { title: "饭" }] }, "poll"],
    [{ type: "app", url: async () => "https://app.example", layout: async () => ({ caption: "卡片" }) }, "app"],
    [{ type: "custom", raw: { imessage_type: "unsupported-message" } }, "unsupported"],
    [{ type: "something-new" }, "unsupported"],
  ];
  for (const [content, expected] of cases) {
    const result = await convertInbound(msg(`c-${expected}`, content), options());
    assert.ok(result.kind === "message", content.type);
    assert.equal(result.data.parts[0]?.type, expected, content.type);
  }
});

test("空白消息也给占位，绝不交出空消息", async () => {
  const result = await convertInbound(msg("m10", { type: "text", text: "   " }), options());
  assert.ok(result.kind === "message");
  assert.deepEqual(result.data.parts, [{ type: "unsupported", what: "空白消息" }]);
});

test("对方点赞、已读、投票选择是控制事件，不当成用户发言", async () => {
  const target = msg("bot-3", { type: "text", text: "晚安" }, { direction: "outbound" });
  const reaction = await convertInbound(msg("r1", { type: "reaction", emoji: "❤️", target }), options());
  assert.deepEqual(reaction.kind === "control" && reaction.data.event, {
    type: "reaction",
    emoji: "❤️",
    target_id: "bot-3",
    target_text: "晚安",
  });
  const read = await convertInbound(msg("r2", { type: "read", target }), options());
  assert.deepEqual(read.kind === "control" && read.data.event, { type: "read", target_id: "bot-3" });
  const vote = await convertInbound(
    msg("r3", { type: "poll_option", option: { title: "面" }, poll: { type: "poll", title: "吃什么", options: [] }, selected: true, title: "面" }),
    options(),
  );
  assert.deepEqual(vote.kind === "control" && vote.data.event, { type: "poll_vote", poll_title: "吃什么", option: "面", selected: true });
});

test("自己发出的消息回显不转发", async () => {
  const result = await convertInbound(msg("o1", { type: "text", text: "我说的" }, { direction: "outbound" }), options());
  assert.equal(result.kind, "echo");
});

test("组合消息的每一项都能被引用或点赞", () => {
  const group: ContentLike = { type: "group", items: [msg("p:0/g", image("a.jpg")), msg("p:1/g", { type: "text", text: "hi" })] };
  const ids = collectMessages(msg("g", group)).map((m) => m.id);
  assert.deepEqual(ids, ["g", "p:0/g", "p:1/g"]);
});
