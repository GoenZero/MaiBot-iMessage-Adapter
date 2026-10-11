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
    plugins: [bundledGrpcPeersPlugin(stats)],
    // 部分依赖是 CommonJS，打包成 ESM 后需要一个 require
    banner: {
      js: [banner, "import { createRequire as __createRequire } from 'node:module';", "const require = __createRequire(import.meta.url);"]
        .filter(Boolean)
        .join("\n"),
    },
  };
}
