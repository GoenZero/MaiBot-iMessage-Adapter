import assert from "node:assert/strict";
import { test } from "node:test";

import { Lanes } from "./lanes.ts";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

test("同一个键按提交顺序执行，不同键互不等待", async () => {
  const lanes = new Lanes(1000);
  const order: string[] = [];
  await Promise.all([
    lanes.run("a", async () => {
      await sleep(30);
      order.push("a1");
    }),
    lanes.run("a", async () => void order.push("a2")),
    lanes.run("b", async () => void order.push("b1")),
  ]);
  assert.deepEqual(order, ["b1", "a1", "a2"]);
  assert.equal(lanes.size, 0);
});

test("前一个任务失败不影响后面的任务", async () => {
  const lanes = new Lanes(1000);
  const first = lanes.run("a", async () => Promise.reject(new Error("boom")));
  const second = lanes.run("a", async () => "ok");
  await assert.rejects(first, /boom/);
  assert.equal(await second, "ok");
});

test("卡住的任务最多挡住后面的任务 holdLimitMs", async () => {
  const lanes = new Lanes(30);
  void lanes.run("a", () => new Promise(() => undefined));
  const started = Date.now();
  assert.equal(await lanes.run("a", async () => "next"), "next");
  assert.ok(Date.now() - started < 500);
});
