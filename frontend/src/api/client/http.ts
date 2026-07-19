import { refreshAccessToken } from '@/api/generated'
import { client } from '@/api/generated/client.gen'

import { ApiError, isApiError, toApiError } from './errors'
import { tokenStore } from './tokenStore'

const MUTATING_METHODS = new Set(['post', 'put', 'patch', 'delete'])

let configured = false

/** 一次性装配：统一信封客户端的请求拦截（Authorization / Request-Id / 写幂等键）。 */
export function configureHttpClient(): void {
  if (configured) {
    return
  }
  configured = true
  client.setConfig({
    baseUrl: typeof window !== 'undefined' ? window.location.origin : '',
    credentials: 'same-origin',
  })
  client.interceptors.request.use((request) => {
    if (!request.headers.has('X-Request-Id')) {
      request.headers.set('X-Request-Id', crypto.randomUUID())
    }
    const token = tokenStore.get()
    if (token && !request.headers.has('Authorization')) {
      request.headers.set('Authorization', `Bearer ${token}`)
    }
    if (MUTATING_METHODS.has(request.method.toLowerCase()) && !request.headers.has('Idempotency-Key')) {
      request.headers.set('Idempotency-Key', crypto.randomUUID())
    }
    return request
  })
}

export interface CallOptions {
  /** 401 时先 single-flight Refresh 再重放一次（默认开启；登录/刷新自身调用须关闭）。 */
  retryOnUnauthorized?: boolean
}

interface RequestOutcome<T> {
  data?: T
  error?: unknown
  response?: Response
}

type RefreshHandler = () => Promise<boolean>

/** 测试可替换的刷新实现；生产使用契约 refreshAccessToken（HttpOnly Cookie + Origin）。 */
let refreshHandler: RefreshHandler = defaultRefresh

async function defaultRefresh(): Promise<boolean> {
  const result = await refreshAccessToken({
    credentials: 'include',
    headers: { Origin: window.location.origin },
  })
  const token = result.data?.data?.access_token
  if (token) {
    tokenStore.set(token)
    return true
  }
  return false
}

export function setRefreshHandler(handler: RefreshHandler): void {
  refreshHandler = handler
}

type SessionExpiredHandler = () => void

let sessionExpiredHandler: SessionExpiredHandler = () => undefined

/** 会话不可恢复时（刷新失败或重放仍 401）触发一次：清理内存身份并跳转登录。 */
export function setSessionExpiredHandler(handler: SessionExpiredHandler): void {
  sessionExpiredHandler = handler
}

let inflightRefresh: Promise<boolean> | null = null

/** 并发 401 共享同一次刷新（single-flight）。 */
export function refreshSession(): Promise<boolean> {
  if (!inflightRefresh) {
    inflightRefresh = refreshHandler().finally(() => {
      inflightRefresh = null
    })
  }
  return inflightRefresh
}

function extractStatus(outcome: { response?: Response }): number {
  return outcome.response?.status ?? 0
}

function toThrown(outcome: RequestOutcome<unknown>): ApiError {
  const requestId = outcome.response?.headers.get('X-Request-Id') ?? null
  return toApiError(extractStatus(outcome), outcome.error, requestId)
}

/** 统一执行生成 SDK 调用：解析错误信封；401 时 single-flight 刷新并重放一次。 */
export async function callApi<T>(execute: () => Promise<RequestOutcome<T>>, options: CallOptions = {}): Promise<T> {
  configureHttpClient()
  const { retryOnUnauthorized = true } = options
  let outcome = await execute()
  if (outcome.error !== undefined && extractStatus(outcome) === 401 && retryOnUnauthorized) {
    const refreshed = await refreshSession()
    if (!refreshed) {
      clearSession()
      sessionExpiredHandler()
    } else {
      outcome = await execute()
      if (outcome.error !== undefined && extractStatus(outcome) === 401) {
        // 重放仍 401：立即过期，绝不二次刷新（防刷新死循环）
        clearSession()
        sessionExpiredHandler()
      }
    }
  }
  if (outcome.error !== undefined) {
    throw toThrown(outcome)
  }
  return outcome.data as T
}

/** 失败即清理内存身份，交由调用方跳转登录。 */
export function clearSession(): void {
  tokenStore.clear()
}

export { ApiError, isApiError }
