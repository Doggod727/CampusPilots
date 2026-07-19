import { setSessionExpiredHandler } from '@/api/client'
import { router } from '@/app/router'
import { useAuthStore } from '@/modules/auth/stores/auth'

/** 会话不可恢复（Cookie 失效/重放仍 401）：清理内存身份并引导回登录页。 */
export function installSessionExpiryGuard(): void {
  setSessionExpiredHandler(() => {
    useAuthStore().expire()
    const current = router.currentRoute.value
    if (current.name !== 'login') {
      void router.replace({ name: 'login', query: { redirect: current.fullPath } })
    }
  })
}
