/**
 * 最近消息句柄缓存（LRU）。
 *
 * 引用、点赞、编辑、撤回都需要拿着 SDK 返回的 Message 对象才能操作。这里按
 * 消息编号保存最近的若干条（收到的和发出的都存），超出容量时淘汰最久没用的。
 * 重启后缓存清空，调用方要能处理「找不到」的情况。
 */
export class LruCache<V> {
  private readonly map = new Map<string, V>();
  private readonly capacity: number;

  constructor(capacity: number) {
    this.capacity = Math.max(1, Math.floor(capacity));
  }

  get size(): number {
    return this.map.size;
  }

  get(key: string): V | undefined {
    const value = this.map.get(key);
    if (value === undefined) return undefined;
    // 重新插入，标记为最近使用
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  has(key: string): boolean {
    return this.map.has(key);
  }

  set(key: string, value: V): void {
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, value);
    while (this.map.size > this.capacity) {
      const oldest = this.map.keys().next();
      if (oldest.done) break;
      this.map.delete(oldest.value);
    }
  }
}
