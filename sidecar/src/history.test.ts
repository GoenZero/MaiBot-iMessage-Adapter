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
  const queries: { after: Date; before: Date; isFromMe?: boolean }[] = [];
  const stored: HistoryMessage[] = [
    message("long", 47, { chatGuids: ["any;-;+81"], content: { text: "1135 字的长消息" }, dateCreated: new Date(start + 84_000) }),
    message("short", 47, { chatGuids: ["any;-;+81"], content: { text: "34 字" }, dateCreated: new Date(start + 82_000) }),
    message("live", 48, { chatGuids: ["any;-;+81"], content: { text: "实时推送到了的" }, dateCreated: new Date(start + 90_000) }),
    message("tapback", 48, { chatGuids: ["any;-;+81"], reaction: {}, dateCreated: new Date(start + 95_000) }),
    message("old", 40, { chatGuids: ["any;-;+81"], content: { text: "侧车启动前的" }, dateCreated: new Date(start - minute) }),
  ];
  const client: HistoryClient = {
    messages: {
      async listInChat() {
        return { messages: [] };
      },
      async listRecent(options) {
        queries.push({ after: options.after!, before: options.before!, isFromMe: options.isFromMe });
        return { messages: stored };
      },
    },
  };
  const delivered: string[] = [];
  const warnings: string[] = [];
  const recoverer = new MissedMessageRecoverer(
    {
      clients: () => [client],
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
  assert.equal(queries[0]!.isFromMe, false);
  assert.equal(queries[0]!.after.getTime(), start);
  assert.equal(queries[0]!.before.getTime(), now - 20_000); // 刚发的先留给实时推送
  // 下一次只往回多看 2 分钟，已经补过的不会再补
  now += 2 * minute;
  assert.equal(await recoverer.check(), 0);
  assert.equal(queries[1]!.after.getTime(), start + 5 * minute - 20_000 - 2 * minute);
  assert.deepEqual(warnings, []);
});

test("recoverer stops quietly when the SDK hides the history API, and survives errors", async () => {
  const warnings: string[] = [];
  const off = new MissedMessageRecoverer({ clients: () => [], wasSeen: () => false, warn: (t) => warnings.push(t), deliver: async () => {} }, 0);
  assert.equal(await off.check(), 0);
  assert.equal(await off.check(), 0);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, /自动补收已停用/);

  const failing: HistoryClient = {
    messages: {
      async listInChat() {
        return { messages: [] };
      },
      async listRecent() {
        throw new Error("UNAVAILABLE");
      },
    },
  };
  const flaky = new MissedMessageRecoverer({ clients: () => [failing], wasSeen: () => false, warn: (t) => warnings.push(t), deliver: async () => {} }, 0);
  assert.equal(await flaky.check(), 0);
  assert.match(warnings[1]!, /没查成，稍后再试：UNAVAILABLE/);
});
