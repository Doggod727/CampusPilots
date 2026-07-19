import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  login: vi.fn(),
  logout: vi.fn(async () => undefined),
  currentUser: vi.fn(),
  restoreSession: vi.fn(),
}))

import { currentUser, login as apiLogin, restoreSession } from '@/api/client'
import { useAuthStore } from '@/modules/auth/stores/auth'

const USER = {
  id: 'u1',
  username: 'student01',
  display_name: '学生一',
  status: 'active',
  roles: [{ id: 'r1', code: 'student', name: '学生' }],
  permissions: ['chat:use', 'service:read'],
  created_at: '2026-07-18T00:00:00Z',
  version: 1,
}

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('logs in and loads the current user into memory', async () => {
    vi.mocked(currentUser).mockResolvedValue(USER as never)
    const store = useAuthStore()
    await store.login('student01', 'secret')
    expect(apiLogin).toHaveBeenCalledWith('student01', 'secret')
    expect(store.user?.username).toBe('student01')
    expect(store.status).toBe('authenticated')
    expect(store.hasPermission('chat:use')).toBe(true)
    expect(store.hasPermission('user:write')).toBe(false)
    expect(store.hasRole('student')).toBe(true)
  })

  it('restores an existing cookie session on bootstrap', async () => {
    vi.mocked(restoreSession).mockResolvedValue(true)
    vi.mocked(currentUser).mockResolvedValue(USER as never)
    const store = useAuthStore()
    expect(await store.restore()).toBe(true)
    expect(store.status).toBe('authenticated')
  })

  it('falls back to anonymous when refresh or user fetch fails', async () => {
    vi.mocked(restoreSession).mockResolvedValue(false)
    const store = useAuthStore()
    expect(await store.restore()).toBe(false)
    expect(store.status).toBe('anonymous')
    expect(store.user).toBeNull()
  })
})
