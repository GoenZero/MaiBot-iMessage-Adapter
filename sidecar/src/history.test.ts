import assert from "node:assert/strict";
import { test } from "node:test";

import { listHistory, MissedMessageRecoverer, photonClients, type HistoryClient, type HistoryMessage } from "./history.ts";

const message = (guid: string, minute: number, extra: Partial<HistoryMessage> = {}): HistoryMessage => ({
  guid,
  dateCreated: new Date(Date.UTC(2026, 9, 19, 4, minute)),
  isFromMe: false,
  content: { text: "", attachments: [] },
  ...extra,
});

test("lists Photon history page by page and marks what the sidecar received", async () => {
  const calls: unknown[] = [];
  const client: HistoryClient = {
    messages: {
      async listInChat(chat, options) {
        calls.push({ chat, ...options });
        if (!options.pageToken) {
          return { messages: [message("b", 47, { content: { text: "很长的一段话🙂" } })], nextPageToken: "next" };
        }
        return { messages: [message("a", 45, { isFromMe: true, content: { text: "", attachments: [{}] } }), message("c", 48, { reaction: {} })] };
      },
    },
  };
  const app = { __internal: { platforms: new Map([["imessage", { client: [{ phone: "shared", client }] }]]) } };
  const result = await listHistory(photonClients(app), "any;-;+81", new Date(0), new Date(1), (id) => id === "a");
  assert.equal(result.truncated, false);
  assert.deepEqual(
    result.messages.map((m) => [m.id, m.from_me, m.kind, m.chars, m.attachments, m.received]),
    [
      ["a", true, "attachment", 0, 1, true],
      ["b", false, "text", 7, 0, false],
      ["c", false, "reaction", 0, 0, false],
    ],
  );
  assert.equal(calls.length, 2);
  assert.equal((calls[0] as { pageSize: number }).pageSize, 100);
});

test("explains when the SDK no longer exposes the client", async () => {
  assert.deepEqual(photonClients({}), []);
  await assert.rejects(listHistory([], "x", new Date(0), new Date(1), () => false), /拿不到 Photon 的消息记录接口/);
});

test("recovers messages Photon stored but never pushed, once and in order", async () => {
  const minute = 60_000;
  const start = Date.UTC(2026, 9, 19, 4, 46);
  let now = start + 5 * minute;
  const seen = new Set(["live"]);
  const queries: { chat: string; after: Date; before: Date }[] = [];
  const stored: HistoryMessage[] = [
    message("long", 47, { chatGuids: ["any;-;+81"], content: { text: "1135 字的长消息" }, dateCreated: new Date(start + 84_000) }),
    message("short", 47, { chatGuids: ["any;-;+81"], content: { text: "34 字" }, dateCreated: new Date(start + 82_000) }),
    message("live", 48, { chatGuids: ["any;-;+81"], content: { text: "实时推送到了的" }, dateCreated: new Date(start + 90_000) }),
    message("tapback", 48, { chatGuids: ["any;-;+81"], reaction: {}, dateCreated: new Date(start + 95_000) }),
    message("mine", 48, { chatGuids: ["any;-;+81"], isFromMe: true, content: { text: "机器人自己发的" }, dateCreated: new Date(start + 96_000) }),
    message("old", 40, { chatGuids: ["any;-;+81"], content: { text: "侧车启动前的" }, dateCreated: new Date(start - minute) }),
  ];
  const client: HistoryClient = {
    messages: {
      async listInChat(chat, options) {
        queries.push({ chat, after: options.after!, before: options.before! });
        return { messages: stored };
      },
    },
  };
  const delivered: string[] = [];
  const warnings: string[] = [];
  const recoverer = new MissedMessageRecoverer(
    {
      clients: () => [client],
      chats: () => ["any;-;+81", "any;-;+81"],
      wasSeen: (id) => seen.has(id),
      warn: (text) => warnings.push(text),
      now: () => now,
      deliver: async (chat, guid, lateMs) => {
        assert.equal(chat, "any;-;+81");
        assert.ok(lateMs > 3 * minute);
        delivered.push(guid);
        seen.add(guid); // 真实流程里 handleInbound 会记下
      },
    },
    start,
  );
  assert.equal(await recoverer.check(), 2);
  assert.deepEqual(delivered, ["short", "long"]);
  assert.equal(queries.length, 1); // 重复的聊天只查一次
  assert.equal(queries[0]!.chat, "any;-;+81");
  assert.equal(queries[0]!.after.getTime(), start);
  assert.equal(queries[0]!.before.getTime(), now - 20_000); // 刚发的先留给实时推送
  // 下一次只往回多看 2 分钟，已经补过的不会再补
  now += 2 * minute;
  assert.equal(await recoverer.check(), 0);
  assert.equal(queries[1]!.after.getTime(), start + 5 * minute - 20_000 - 2 * minute);
  assert.deepEqual(warnings, []);
});

test("recoverer asks Photon only as often as needed", async () => {
  const minute = 60_000;
  const start = 1_000_000_000_000;
  let now = start;
  let calls = 0;
  const client: HistoryClient = {
    messages: {
      async listInChat() {
        calls += 1;
        return { messages: [] };
      },
    },
  };
  let chats: string[] = [];
  const recoverer = new MissedMessageRecoverer(
    { clients: () => [client], chats: () => chats, wasSeen: () => false, warn: () => {}, now: () => now, deliver: async () => {} },
    start,
  );
  const tickAt = async (minutes: number) => {
    now = start + minutes * minute;
    await recoverer.tick();
  };
  // 还不知道有哪些聊天：不发请求
  await tickAt(2);
  assert.equal(calls, 0);
  chats = ["any;-;+81"];
  // 刚启动的 30 分钟内每 2 分钟一次
  await tickAt(3);
  assert.equal(calls, 0);
  await tickAt(4);
  assert.equal(calls, 1);
  await tickAt(6);
  assert.equal(calls, 2);
  // 之后既没重连也没人聊天：30 分钟一次
  await tickAt(32);
  assert.equal(calls, 2);
  await tickAt(36);
  assert.equal(calls, 3);
  await tickAt(50);
  assert.equal(calls, 3);
  await tickAt(66);
  assert.equal(calls, 4);
  // 有人聊天：10 分钟一次
  recoverer.noteActivity();
  await tickAt(70);
  assert.equal(calls, 4);
  await tickAt(76);
  assert.equal(calls, 5);
  // 断线恢复：又回到 2 分钟一次
  recoverer.noteReconnect();
  await tickAt(78);
  assert.equal(calls, 6);
  assert.equal(recoverer.intervalNow(), 2 * minute);
});

test("recoverer stops quietly when the SDK hides the history API", async () => {
  const warnings: string[] = [];
  const off = new MissedMessageRecoverer(
    { clients: () => [], chats: () => ["any;-;+81"], wasSeen: () => false, warn: (t) => warnings.push(t), deliver: async () => {} },
    0,
  );
  assert.equal(await off.check(), 0);
  assert.equal(await off.tick(), 0);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, /自动补收已停用/);
});

test("recoverer backs off after errors, logs once, and says when it works again", async () => {
  const minute = 60_000;
  const start = 1_000_000_000_000;
  let now = start + 2 * minute;
  let failing = true;
  let calls = 0;
  const client: HistoryClient = {
    messages: {
      async listInChat() {
        calls += 1;
        if (failing) throw new Error("No instance routed for this request");
        return { messages: [] };
      },
    },
  };
  const warnings: string[] = [];
  const infos: string[] = [];
  const recoverer = new MissedMessageRecoverer(
    {
      clients: () => [client],
      chats: () => ["any;-;+81"],
      wasSeen: () => false,
      warn: (t) => warnings.push(t),
      info: (t) => infos.push(t),
      now: () => now,
      deliver: async () => {},
    },
    start,
  );
  assert.equal(await recoverer.tick(), 0);
  assert.equal(calls, 1);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, /没查成，之后放慢重试（最长 30 分钟一次）.*No instance routed/);
  // 退避：2、4、8、16、30、30 分钟
  const waits: number[] = [];
  for (let i = 0; i < 6; i += 1) {
    const callsBefore: number = calls;
    let waited = 0;
    while (calls === callsBefore) {
      waited += 1;
      now += minute;
      await recoverer.tick();
    }
    waits.push(waited);
  }
  assert.deepEqual(waits, [2, 4, 8, 16, 30, 30]);
  assert.equal(warnings.length, 1); // 连续失败只记第一次
  failing = false;
  now += 30 * minute;
  await recoverer.tick();
  assert.deepEqual(infos, ["漏消息自动补收恢复正常（之前连续 7 次没查成）"]);
  assert.equal(warnings.length, 1);
});
