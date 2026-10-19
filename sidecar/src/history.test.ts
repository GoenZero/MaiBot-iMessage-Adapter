import assert from "node:assert/strict";
import { test } from "node:test";

import { listHistory, photonClients, type HistoryClient, type HistoryMessage } from "./history.ts";

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
