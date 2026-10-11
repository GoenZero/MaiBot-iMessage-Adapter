// 把侧车打包成单个文件 dist/sidecar.mjs，并汇总第三方许可证。
//
// 用法：
//   node build.mjs           打包并写入 dist/
//   node build.mjs --verify  重新打包到临时目录，检查与 dist/ 中已提交的文件是否一致
//
// 树莓派上不运行本脚本：dist/ 随仓库提交，设备上只需要 node 运行 dist/sidecar.mjs。
import { build } from "esbuild";

import { buildOptions } from "./build-config.mjs";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const verify = process.argv.includes("--verify");
const outDir = verify ? mkdtempSync(join(tmpdir(), "sidecar-verify-")) : join(here, "dist");
mkdirSync(outDir, { recursive: true });

const lock = JSON.parse(readFileSync(join(here, "package-lock.json"), "utf8"));
const spectrumVersion = lock.packages["node_modules/@spectrum-ts/core"].version;

const stats = { patched: 0 };
const result = await build(
  buildOptions({
    entryPoints: [join(here, "src/main.ts")],
    outfile: join(outDir, "sidecar.mjs"),
    define: { __SPECTRUM_VERSION__: JSON.stringify(spectrumVersion) },
    banner: "// MaiBot iMessage 适配器侧车（自动生成，请勿手改；源码见 sidecar/src，许可证见 THIRD_PARTY_LICENSES.txt）",
    stats,
  }),
);
if (stats.patched !== 1) {
  console.error(`advanced-imessage 的 gRPC 依赖检查修补次数为 ${stats.patched}，应为 1`);
  process.exit(1);
}
if (stats.pollPatched !== 1 || stats.changePatched !== 1) {
  console.error(`@spectrum-ts/imessage 的修补次数不对（投票标题 ${stats.pollPatched ?? 0}，编辑撤回 ${stats.changePatched ?? 0}），都应为 1`);
  process.exit(1);
}

// ---- 汇总被打包进去的第三方包的许可证 ----
const packageRoots = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  const match = input.match(/^(.*node_modules\/(?:@[^/]+\/)?[^/]+)\//);
  if (match) packageRoots.add(match[1]);
}
const sections = [];
for (const root of [...packageRoots].sort()) {
  const pkg = JSON.parse(readFileSync(join(here, root, "package.json"), "utf8"));
  const licenseFile = readdirSync(join(here, root)).find((name) => /^(licen[cs]e|copying)/i.test(name));
  const text = licenseFile ? readFileSync(join(here, root, licenseFile), "utf8").trim() : `License: ${pkg.license ?? "unknown"}`;
  sections.push(`${"=".repeat(72)}\n${pkg.name}@${pkg.version} (${pkg.license ?? "unknown"})\n${"=".repeat(72)}\n${text}\n`);
}
writeFileSync(
  join(outDir, "THIRD_PARTY_LICENSES.txt"),
  `dist/sidecar.mjs 中包含以下第三方开源软件，按各自许可证分发。\n共 ${sections.length} 个包。\n\n${sections.join("\n")}`,
);

if (verify) {
  let mismatch = false;
  for (const name of ["sidecar.mjs", "THIRD_PARTY_LICENSES.txt"]) {
    const committed = join(here, "dist", name);
    if (!existsSync(committed) || !readFileSync(committed).equals(readFileSync(join(outDir, name)))) {
      console.error(`dist/${name} 与源码不一致，请运行 npm run build 后一起提交`);
      mismatch = true;
    }
  }
  rmSync(outDir, { recursive: true, force: true });
  if (mismatch) process.exit(1);
  console.log("dist/ 与源码一致");
} else {
  const size = readFileSync(join(outDir, "sidecar.mjs")).length;
  console.log(`已生成 dist/sidecar.mjs（${(size / 1024 / 1024).toFixed(1)} MB，spectrum-ts ${spectrumVersion}，${sections.length} 个第三方包）`);
}
