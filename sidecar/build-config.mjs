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
 * 对 @spectrum-ts/imessage 的三处修补（真机测试发现），都在打包时完成，找不到要改的代码时让打包失败：
 *
 * 1. 投票标题：Photon 送回来的投票（包括 AI 自己发起的）标题是空的，而 SDK 缓存投票时要求标题非空，
 *    缓存失败后对方的投票事件全部被丢掉。让空标题变成 UNTITLED_POLL，侧车再把真正的标题补回来
 *    （见 src/polls.ts）。
 * 2. 对方编辑、撤回消息、取消点赞：Photon 的消息流里有这三种事件（message.edited / message.unsent /
 *    message.reactionRemoved），但 SDK 不转发，只记一条调试日志。这里把它们转成 custom 内容
 *    （raw.imessage_change 标明是哪种），侧车再变成控制事件（见 src/convert.ts）。机器人自己的操作不转发。
 * 3. 对方发起投票：SDK 只把投票记进缓存，不转发（真机第三轮发现，AI 看不到对方发起的投票）。
 *    这里把「投票已创建」转成一条普通的投票消息。机器人自己发起的投票在外层已经被过滤掉。
 */
const CHANGE_FORWARDER = `
	if (event.type === "message.edited" || event.type === "message.unsent" || event.type === "message.reactionRemoved") {
		const changeId = \`\${event.messageGuid}:\${event.type}:\${event.sequence}\`;
		if (event.isFromMe) return { cursor, id: changeId, values: [] };
		const peer = dmPeerFromChatGuid(event.chatGuid);
		return { cursor, id: changeId, values: [{
			id: changeId,
			sender: peer ? { id: peer, address: peer } : toSenderRef(event.actor),
			space: { id: event.chatGuid, type: chatTypeFromGuid(event.chatGuid), phone },
			timestamp: event.occurredAt,
			content: asCustom({
				imessage_change: event.type,
				message_guid: event.messageGuid,
				text: event.content?.text,
				reaction: event.reaction ? (reactionEmoji(event.reaction) ?? event.reaction.kind) : undefined,
				part_index: event.targetPartIndex
			})
		}] };
	}
`;

const POLL_CREATED_FORWARDER = `
	if (event.delta.type === "created") {
		const created = pollCache.get(event.pollMessageGuid);
		if (!created) return [];
		const peer = dmPeerFromChatGuid(event.chatGuid);
		return [{
			id: event.pollMessageGuid,
			sender: peer ? { id: peer, address: peer } : toSenderRef(event.actor),
			space: { id: event.chatGuid, type: chatTypeFromGuid(event.chatGuid), phone },
			timestamp: event.occurredAt,
			content: created.poll
		}];
	}
`;

/** 对 @spectrum-ts/imessage/dist/index.js 的源码做上面几处修补；找不到要改的代码时抛错。 */
export function patchImessageSource(source, stats = {}) {
  const withPollFix = source.replace(
    /(const toCachedPoll = \(input\) => \{\s*const poll = asPoll\(\{\s*title: )input\.title,/,
    `$1input.title || ${JSON.stringify(UNTITLED_POLL)},`,
  );
  if (withPollFix === source) {
    throw new Error("无法修补 toCachedPoll，请检查 @spectrum-ts/imessage 是否改了实现");
  }
  stats.pollPatched = (stats.pollPatched ?? 0) + 1;
  const anchor = '\tstreamLog.debug("message event consumed without mapping", {';
  const parts = withPollFix.split(anchor);
  if (parts.length !== 2) {
    throw new Error(`无法修补 toMessageItem（锚点出现 ${parts.length - 1} 次），请检查 @spectrum-ts/imessage 是否改了实现`);
  }
  stats.changePatched = (stats.changePatched ?? 0) + 1;
  const withChanges = parts[0] + CHANGE_FORWARDER + anchor + parts[1];
  const pollAnchor = "const toPollDeltaMessages = async (client, pollCache, event, phone) => {";
  const pollParts = withChanges.split(pollAnchor);
  if (pollParts.length !== 2) {
    throw new Error(`无法修补 toPollDeltaMessages（锚点出现 ${pollParts.length - 1} 次），请检查 @spectrum-ts/imessage 是否改了实现`);
  }
  stats.pollCreatedPatched = (stats.pollCreatedPatched ?? 0) + 1;
  return pollParts[0] + pollAnchor + POLL_CREATED_FORWARDER + pollParts[1];
}

export function imessagePatchesPlugin(stats) {
  return {
    name: "imessage-patches",
    setup(build) {
      build.onLoad({ filter: /@spectrum-ts[\\/]imessage[\\/]dist[\\/]index\.js$/ }, async (args) => {
        const source = await readFile(args.path, "utf8");
        return { contents: patchImessageSource(source, stats), loader: "js" };
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
    plugins: [bundledGrpcPeersPlugin(stats), imessagePatchesPlugin(stats)],
    // 部分依赖是 CommonJS，打包成 ESM 后需要一个 require
    banner: {
      js: [banner, "import { createRequire as __createRequire } from 'node:module';", "const require = __createRequire(import.meta.url);"]
        .filter(Boolean)
        .join("\n"),
    },
  };
}
