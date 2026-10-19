// 测试用的假侧车：实现与真侧车相同的协议，不连 Photon。
import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";

const mode = process.env.FAKE_MODE ?? "ok";
const frame = (obj) => process.stdout.write(JSON.stringify({ imsg: 1, ...obj }) + "\n");

if (mode === "auth") {
  frame({ t: "evt", event: "fatal", data: { code: "AUTH", message: "bad secret" } });
  process.exit(2);
}
if (mode === "crash-on-start") process.exit(1);

process.stderr.write("WARN 假侧车启动 secret=" + (process.env.PHOTON_PROJECT_SECRET ?? "") + "\n");
process.stdout.write("这是一行不是协议的输出\n");
frame({ t: "evt", event: "ready", data: { spectrum: "fake", node: process.version } });
if (process.env.FAKE_EMIT === "1") {
  for (let i = 1; i <= 3; i++) {
    frame({ t: "evt", event: "message", data: { message_id: `m${i}`, chat_id: "any;-;+1", sender_id: "+1", timestamp: 0, parts: [{ type: "text", text: `第${i}条` }] } });
  }
}

if (process.env.FAKE_RECOVERED === "1") {
  frame({ t: "evt", event: "message", data: { message_id: "late1", chat_id: "any;-;+1", sender_id: "+1", timestamp: 0, recovered: true, parts: [{ type: "text", text: "Photon 漏推、补收回来的" }] } });
}

if (mode === "stall-input") {
  // 报告 ready 后不再读取输入：用来测试 Python 写管道卡住时的总时限
  process.stdin.pause();
  setInterval(() => {}, 1000);
} else {
const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const req = JSON.parse(line);
  if (process.env.FAKE_LOG) appendFileSync(process.env.FAKE_LOG, JSON.stringify(req) + "\n");
  switch (req.op) {
    case "react":
      if (req.args.message_id === "missing") {
        frame({ t: "res", id: req.id, ok: false, error: { code: "MESSAGE_NOT_FOUND", message: "找不到" } });
      } else {
        frame({ t: "res", id: req.id, ok: true, result: { reaction_id: "rx-" + req.id } });
      }
      break;
    case "send": {
      // 每个片段各发成一条，编号 out-<请求号>、out-<请求号>-1……
      const parts = req.args.parts?.length ? req.args.parts : [null];
      const ids = parts.map((_, i) => (i === 0 ? `out-${req.id}` : `out-${req.id}-${i}`));
      frame({ t: "res", id: req.id, ok: true, result: { message_ids: ids, quoted: Boolean(req.args.reply_to), partial: false } });
      break;
    }
    case "edit":
    case "unsend":
      if (req.args.message_id === "missing") {
        frame({ t: "res", id: req.id, ok: false, error: { code: "MESSAGE_NOT_FOUND", message: "找不到" } });
      } else if (String(req.args.message_id).startsWith("in-")) {
        frame({ t: "res", id: req.id, ok: false, error: { code: "NOT_OWN_MESSAGE", message: "不是自己的" } });
      } else if (req.args.message_id === "late") {
        frame({ t: "res", id: req.id, ok: false, error: { code: "PHOTON_ERROR", message: "unsend window expired" } });
      } else {
        frame({ t: "res", id: req.id, ok: true, result: {} });
      }
      break;
    case "list_messages": {
      const at = (offset) => req.args.after + offset;
      frame({ t: "res", id: req.id, ok: true, result: {
        sidecar_started_at: req.args.after - 1,
        truncated: false,
        messages: [
          { id: "m1", time: at(60000), from_me: false, kind: "text", chars: 12, attachments: 0, received: true },
          { id: "m2", time: at(120000), from_me: false, kind: "text", chars: 523, attachments: 0, received: false },
          { id: "m3", time: at(180000), from_me: true, kind: "attachment", chars: 0, attachments: 1, received: false },
        ],
      } });
      break;
    }
    case "background":
      if (req.args.message_id === "txt") {
        frame({ t: "res", id: req.id, ok: false, error: { code: "NO_IMAGE", message: "没有图片" } });
      } else {
        frame({ t: "res", id: req.id, ok: true, result: {} });
      }
      break;
    case "malformed":
      // 合法 JSON，但回执编号是对象
      frame({ t: "res", id: { bad: 1 }, ok: true, result: {} });
      frame({ t: "res", id: req.id, ok: true, result: { after: "malformed" } });
      break;
    case "leak":
      frame({ t: "res", id: req.id, ok: false, error: { code: "INTERNAL", message: "failure: " + (process.env.PHOTON_PROJECT_SECRET ?? "") } });
      break;
    case "burst":
      for (let i = 0; i < req.args.count; i++) {
        frame({ t: "evt", event: "message", data: { message_id: `burst-${i}`, chat_id: "any;-;+1", sender_id: "+1", timestamp: 0, parts: [{ type: "text", text: "x" }] } });
      }
      frame({ t: "res", id: req.id, ok: true, result: {} });
      break;
    case "fail":
      frame({ t: "res", id: req.id, ok: false, error: { code: "TARGET_NOT_ALLOWED", message: "Target not allowed" } });
      break;
    case "slow":
      break; // 永不回应
    case "crash":
      process.exit(1);
      break;
    case "shutdown":
      frame({ t: "res", id: req.id, ok: true, result: {} });
      setImmediate(() => process.exit(0));
      break;
    default:
      frame({ t: "res", id: req.id, ok: true, result: { echo: req.op } });
  }
});
rl.on("close", () => process.exit(0));
}
