/** 浏览器持久化运行时护栏：业务与三方库的 Storage/IndexedDB/CacheStorage 写操作立即失败。 */
export function installNoPersistenceGuard(): void {
  if (typeof window === 'undefined') {
    return
  }
  const blocked = (name: string) => () => {
    throw new Error(`${name} 已按安全策略禁用`)
  }
  const storageProto = window.Storage?.prototype
  if (storageProto) {
    for (const method of ['setItem', 'removeItem', 'clear'] as const) {
      try {
        Object.defineProperty(storageProto, method, {
          value: blocked(`Storage.${method}`),
          configurable: true,
        })
      } catch {
        // 宿主不允许覆写原型时退化为实例级覆写
      }
    }
  }
  for (const storage of [window.localStorage, window.sessionStorage]) {
    try {
      Object.defineProperty(storage, 'setItem', { value: blocked('Storage.setItem') })
      Object.defineProperty(storage, 'removeItem', { value: blocked('Storage.removeItem') })
      Object.defineProperty(storage, 'clear', { value: blocked('Storage.clear') })
    } catch {
      // 只读宿主环境：跳过实例级覆写
    }
  }
  const forbid = (name: string) => () => {
    throw new Error(`${name} 已按安全策略禁用`)
  }
  if ('indexedDB' in window) {
    Object.defineProperty(window, 'indexedDB', { get: forbid('indexedDB') })
  }
  if ('caches' in window) {
    Object.defineProperty(window, 'caches', { get: forbid('caches') })
  }
}
