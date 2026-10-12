/**
 * 按键排队：同一个键的任务一个接一个执行，不同键互不影响。
 *
 * 用来保证同一个聊天里的发送、编辑、撤回等按收到的顺序执行（外部审查发现：
 * 先提交的图片要先读文件，后提交的文字可能抢先发出）。
 *
 * 一个任务最多占住队伍 holdLimitMs 毫秒：卡住的任务不会让这个聊天永远发不出消息。
 * 超时后任务本身照常继续，只是不再挡着后面的任务。
 */
export class Lanes {
  private readonly tails = new Map<string, Promise<void>>();
  private readonly holdLimitMs: number;

  constructor(holdLimitMs: number) {
    this.holdLimitMs = holdLimitMs;
  }

  run<T>(key: string, work: () => Promise<T>): Promise<T> {
    const previous = this.tails.get(key) ?? Promise.resolve();
    let release!: () => void;
    const slot = new Promise<void>((resolve) => (release = resolve));
    const tail = previous.then(() => slot);
    this.tails.set(key, tail);
    void tail.then(() => {
      if (this.tails.get(key) === tail) this.tails.delete(key);
    });
    return previous.then(async () => {
      const timer = setTimeout(release, this.holdLimitMs);
      try {
        return await work();
      } finally {
        clearTimeout(timer);
        release();
      }
    });
  }

  /** 正在排队或执行的键数（测试用） */
  get size(): number {
    return this.tails.size;
  }
}
