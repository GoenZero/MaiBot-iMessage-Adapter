/**
 * 投票标题。
 *
 * 实测（真机第二轮）：Photon 送回来的投票事件里标题是空的，spectrum-ts 缓存投票时要求标题非空，
 * 缓存失败后，对方的每一次投票都会被丢掉。打包时把 SDK 里的空标题换成 UNTITLED_POLL（见
 * build-config.mjs 的 imessagePatchesPlugin），对方的投票就能正常送到；标题再从这里补回来：
 * AI 发起的投票，发送时记下「投票消息编号 → 标题」。
 */

/** 与 build-config.mjs 里的同名常量保持一致。 */
export const UNTITLED_POLL = "__imessage_untitled_poll__";

const MAX_POLLS = 100;
const titles = new Map<string, string>();

export function rememberSentPoll(messageId: string, title: string): void {
  titles.delete(messageId);
  titles.set(messageId, title);
  if (titles.size > MAX_POLLS) {
    const oldest = titles.keys().next();
    if (!oldest.done) titles.delete(oldest.value);
  }
}

/**
 * 按投票事件的编号找标题。SDK 给投票选择事件编的号是「投票消息编号:投票人:选项:动作:时间」。
 */
export function sentPollTitle(eventId: string): string | undefined {
  for (const [messageId, title] of titles) {
    if (eventId === messageId || eventId.startsWith(`${messageId}:`)) return title;
  }
  return undefined;
}
