import { defineStore } from 'pinia'

import {
  currentUser as fetchCurrentUser,
  login as apiLogin,
  logout as apiLogout,
  restoreSession,
} from '@/api/client'
import type { CurrentUser } from '@/api/generated'

export type AuthStatus = 'unknown' | 'authenticated' | 'anonymous'

interface AuthState {
  user: CurrentUser | null
  status: AuthStatus
}

/** 认证状态只驻留 Pinia 内存：页面刷新经 Refresh Cookie + getCurrentUser 重建。 */
export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    user: null,
    status: 'unknown',
  }),
  getters: {
    permissions(state): ReadonlySet<string> {
      return new Set(state.user?.permissions ?? [])
    },
    roles(state): ReadonlySet<string> {
      return new Set((state.user?.roles ?? []).map((role) => role.code))
    },
  },
  actions: {
    hasPermission(code: string): boolean {
      return this.permissions.has(code)
    },
    hasRole(code: string): boolean {
      return this.roles.has(code)
    },
    async login(username: string, password: string): Promise<void> {
      await apiLogin(username, password)
      this.user = await fetchCurrentUser()
      this.status = 'authenticated'
    },
    async restore(): Promise<boolean> {
      const refreshed = await restoreSession()
      if (!refreshed) {
        this.status = 'anonymous'
        this.user = null
        return false
      }
      try {
        this.user = await fetchCurrentUser()
        this.status = 'authenticated'
        return true
      } catch {
        this.status = 'anonymous'
        this.user = null
        return false
      }
    },
    async logout(): Promise<void> {
      await apiLogout()
      this.user = null
      this.status = 'anonymous'
    },
    /** 会话不可恢复时由 HTTP 层调用：仅清理内存，不通知后端。 */
    expire(): void {
      this.user = null
      this.status = 'anonymous'
    },
  },
})
