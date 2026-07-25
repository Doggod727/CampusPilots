import { useAuthStore } from '@/modules/auth/stores/auth'

let bootstrapped: Promise<boolean> | null = null

/** 应用启动时仅执行一次会话恢复（并发导航共享同一 Promise）。 */
export function bootstrapAuth(): Promise<boolean> {
  if (!bootstrapped) {
    bootstrapped = useAuthStore().restore()
  }
  return bootstrapped
}
