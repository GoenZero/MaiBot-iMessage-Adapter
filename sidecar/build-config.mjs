// 打包配置（build.mjs 和打包回归测试共用）。
import { readFile } from "node:fs/promises";

/**
 * @photon-ai/advanced-imessage 在建立 gRPC 连接前，会用 import.meta.resolve 检查
 * nice-grpc / nice-grpc-common / @grpc/grpc-js 能否在「文件旁边」找到。打包后这三个包
 * 已经在 sidecar.mjs 里面，但设备上的 dist/ 旁边没有 node_modules，检查必然失败，
 * 侧车连不上 Photon（0.2.0 首次真机部署时发现）。这里在打包时把这个检查去掉。
 *
 * 找不到要改的代码时直接让打包失败，防止升级依赖后悄悄失效。
 */
export function bundledGrpcPeersPlugin(stats) {
  return {
    name: "bundled-grpc-peers",
    setup(build) {
      build.onLoad({ filter: /@photon-ai[\\/]advanced-imessage[\\/]dist[\\/].*\.js$/ }, async (args) => {
        const source = await readFile(args.path, "utf8");
        if (!source.includes("function assertPeersResolvable(")) return undefined;
        const patched = source.replace(
          /(function assertPeersResolvable\(\)\s*\{\s*const meta = )import\.meta;/,
          "$1{}; /* 已打包进 sidecar.mjs，无需在磁盘上解析 */",
        );
        if (patched === source) {
          throw new Error(`无法修补 ${args.path} 中的 assertPeersResolvable，请检查 @photon-ai/advanced-imessage 是否改了实现`);
        }
        stats.patched += 1;
        return { contents: patched, loader: "js" };
      });
    },
  };
}

/** 与 src/polls.ts 里的同名常量保持一致。 */
export const UNTITLED_POLL = "__imessage_untitled_poll__";

/**
 * Photon 送回来的投票（包括 AI 自己发起的）标题是空的，而 @spectrum-ts/imessage 缓存投票时
 * 要求标题非空：缓存失败后，对方的投票事件全部被丢掉（真机第二轮发现）。这里在打包时让空标题
 * 变成 UNTITLED_POLL，侧车再把真正的标题补回来（见 src/polls.ts）。
 *
 * 找不到要改的代码时直接让打包失败。
 */
export function untitledPollPlugin(stats) {
  return {
    name: "untitled-poll",
    setup(build) {
      build.onLoad({ filter: /@spectrum-ts[\\/]imessage[\\/]dist[\\/]index\.js$/ }, async (args) => {
        const source = await readFile(args.path, "utf8");
        const patched = source.replace(
          /(const toCachedPoll = \(input\) => \{\s*const poll = asPoll\(\{\s*title: )input\.title,/,
          `$1input.title || ${JSON.stringify(UNTITLED_POLL)},`,
        );
        if (patched === source) {
          throw new Error(`无法修补 ${args.path} 中的 toCachedPoll，请检查 @spectrum-ts/imessage 是否改了实现`);
        }
        stats.pollPatched = (stats.pollPatched ?? 0) + 1;
        return { contents: patched, loader: "js" };
      });
    },
  };
}

/** 生成 esbuild 选项。 */
export function buildOptions({ entryPoints, outfile, define = {}, banner = "", stats }) {
  return {
    entryPoints,
    outfile,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node22",
    minifyWhitespace: true,
    minifySyntax: true,
    keepNames: true,
    legalComments: "none",
    metafile: true,
    logLevel: "warning",
    define,
    plugins: [bundledGrpcPeersPlugin(stats), untitledPollPlugin(stats)],
    // 部分依赖是 CommonJS，打包成 ESM 后需要一个 require
    banner: {
      js: [banner, "import { createRequire as __createRequire } from 'node:module';", "const require = __createRequire(import.meta.url);"]
        .filter(Boolean)
        .join("\n"),
    },
  };
}
