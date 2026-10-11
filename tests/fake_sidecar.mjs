// 测试用的假侧车：实现与真侧车相同的协议，不连 Photon。
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
  switch (req.op) {
    case "send":
      frame({ t: "res", id: req.id, ok: true, result: { message_ids: ["out-" + req.id], quoted: Boolean(req.args.reply_to), partial: false } });
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
