import { getCurrentUser, login as sdkLogin, logout as sdkLogout } from '@/api/generated'

import { callApi, clearSession, refreshSession } from './http'
import { tokenStore } from './tokenStore'

/** 登录：成功后将 Access Token 存入内存（Cookie 由后端设置）。 */
export async function login(username: string, password: string) {
  const response = await callApi(() => sdkLogin({ body: { username, password } }), {
    retryOnUnauthorized: false,
  })
  tokenStore.set(response.data.access_token)
  return response.data
}

/** 页面启动恢复：凭 Refresh Cookie 换发新 Access Token；失败返回 false 由调用方跳登录。 */
export async function restoreSession(): Promise<boolean> {
  return refreshSession()
}

export async function currentUser() {
  const response = await callApi(() => getCurrentUser())
  return response.data
}

/** 幂等登出：先通知后端，再清理内存身份（即使失败也清理）。 */
export async function logout(): Promise<void> {
  try {
    await callApi(
      () =>
        sdkLogout({
          credentials: 'include',
          headers: { Origin: window.location.origin },
        }),
      { retryOnUnauthorized: false },
    )
  } finally {
    clearSession()
  }
}
