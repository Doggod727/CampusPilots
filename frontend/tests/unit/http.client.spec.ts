import { beforeEach, describe, expect, it, vi } from 'vitest'

import { callApi, refreshSession, setRefreshHandler } from '@/api/client/http'
import { isApiError } from '@/api/client/errors'
import { tokenStore } from '@/api/client/tokenStore'

function outcome<T>(data: T, status = 200) {
  return { data, error: undefined, response: new Response(null, { status }) }
}

function failure(status: number, code = 'SOME_ERROR', message = '请求失败') {
  return {
    data: undefined,
    error: { code, message, details: [{ field: 'name', reason: '无效' }], request_id: 'req-test' },
    response: new Response(null, { status }),
  }
}

beforeEach(() => {
  tokenStore.clear()
  setRefreshHandler(async () => true)
})

describe('callApi success', () => {
  it('returns response data', async () => {
    const result = await callApi(async () => outcome({ id: 1 }))
    expect(result).toEqual({ id: 1 })
  })
})

describe('callApi error mapping', () => {
  it.each([
    [401, 'AUTH_UNAUTHORIZED'],
    [403, 'AUTH_FORBIDDEN'],
    [409, 'CONFLICT'],
    [422, 'VALIDATION_ERROR'],
    [429, 'RATE_LIMITED'],
    [502, 'AGENT_PROVIDER_UNAVAILABLE'],
    [503, 'SERVICE_NOT_READY'],
    [504, 'AGENT_PROVIDER_TIMEOUT'],
  ])('maps %i %s to ApiError without leaking internals', async (status, code) => {
    const error = await callApi(async () => failure(status, code), { retryOnUnauthorized: false }).catch(
      (value: unknown) => value,
    )
    expect(isApiError(error)).toBe(true)
    if (isApiError(error)) {
      expect(error.status).toBe(status)
      expect(error.code).toBe(code)
      expect(error.details[0]).toEqual({ field: 'name', reason: '无效' })
      expect(error.requestId).toBe('req-test')
      expect(error.message).not.toContain('token')
    }
  })
})

describe('401 single-flight refresh', () => {
  it('replays once after a shared refresh and returns data', async () => {
    const refresh = vi.fn(async () => true)
    setRefreshHandler(refresh)
    tokenStore.set('old-token')
    const execute = vi
      .fn()
      .mockResolvedValueOnce(failure(401, 'AUTH_UNAUTHORIZED'))
      .mockResolvedValueOnce(failure(401, 'AUTH_UNAUTHORIZED'))
      .mockResolvedValueOnce(outcome({ ok: true }))
      .mockResolvedValueOnce(outcome({ ok: true }))
    const [first, second] = await Promise.all([
      callApi(execute),
      callApi(execute),
    ])
    expect(first).toEqual({ ok: true })
    expect(second).toEqual({ ok: true })
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledTimes(4)
  })

  it('does not replay when refresh fails', async () => {
    setRefreshHandler(async () => false)
    tokenStore.set('stale')
    const execute = vi.fn().mockResolvedValue(failure(401, 'AUTH_UNAUTHORIZED'))
    const error = await callApi(execute).catch((value: unknown) => value)
    expect(isApiError(error)).toBe(true)
    expect(execute).toHaveBeenCalledTimes(1)
    expect(tokenStore.get()).toBeNull()
  })

  it('expires the session without a second refresh when the replay is still 401', async () => {
    const refresh = vi.fn(async () => true)
    setRefreshHandler(refresh)
    tokenStore.set('stale')
    const execute = vi.fn().mockResolvedValue(failure(401, 'AUTH_UNAUTHORIZED'))
    const error = await callApi(execute).catch((value: unknown) => value)
    expect(isApiError(error)).toBe(true)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledTimes(2)
    expect(tokenStore.get()).toBeNull()
  })

  it('skips refresh entirely when disabled', async () => {
    const refresh = vi.fn(async () => true)
    setRefreshHandler(refresh)
    const error = await callApi(async () => failure(401, 'AUTH_UNAUTHORIZED'), {
      retryOnUnauthorized: false,
    }).catch((value: unknown) => value)
    expect(isApiError(error)).toBe(true)
    expect(refresh).not.toHaveBeenCalled()
  })
})

describe('refreshSession single-flight', () => {
  it('shares one refresh across concurrent callers', async () => {
    let release: (value: boolean) => void = () => undefined
    const refresh = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          release = resolve
        }),
    )
    setRefreshHandler(refresh)
    const first = refreshSession()
    const second = refreshSession()
    release(true)
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(refresh).toHaveBeenCalledTimes(1)
  })
})
