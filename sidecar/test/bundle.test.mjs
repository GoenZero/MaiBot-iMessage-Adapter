// 打包回归测试：打包后的代码放到一个旁边没有 node_modules 的目录里（和设备上一样），
// 确认 gRPC 客户端能创建出来。对应 0.2.0 首次真机部署时
// 「createGrpcClient needs the optional peer dependencies」导致连不上 Photon 的问题。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
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
