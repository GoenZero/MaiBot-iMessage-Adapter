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
