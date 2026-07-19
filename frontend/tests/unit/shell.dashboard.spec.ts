import { mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '@/app/layouts/AppShell.vue'
import DashboardView from '@/app/router/DashboardView.vue'
import { tokenStore } from '@/api/client'
import { useAuthStore } from '@/modules/auth/stores/auth'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ADMIN = {
  id: 'a1',
  username: 'admin01',
  display_name: '管理员',
  status: 'active',
  roles: [{ id: 'r0', code: 'super_admin', name: '超级管理员' }],
  permissions: ['dashboard:read', 'user:read', 'config:read', 'chat:use'],
  created_at: '2026-07-18T00:00:00Z',
  version: 1,
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: { template: '<p>login</p>' } },
      { path: '/', name: 'dashboard', component: DashboardView },
      { path: '/chat', name: 'chat', component: { template: '<p>chat</p>' } },
      { path: '/admin/users', name: 'admin-users', component: { template: '<p>users</p>' } },
    ],
  })
}

describe('AppShell', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders permitted nav groups and performs logout', async () => {
    const store = useAuthStore()
    store.user = ADMIN as never
    store.status = 'authenticated'
    store.logout = vi.fn().mockImplementation(async () => {
      store.user = null
      store.status = 'anonymous'
    })
    const router = makeRouter()
    await router.push('/')
    const wrapper = mount(AppShell, { global: { plugins: [router] } })
    expect(wrapper.text()).toContain('概览')
    expect(wrapper.text()).toContain('管理')
    expect(wrapper.text()).toContain('管理员')
    await wrapper.find('.shell__user .ui-button--text').trigger('click')
    expect(store.logout).toHaveBeenCalled()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
  })
})

describe('DashboardView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows metrics for dashboard:read users from the real envelope', async () => {
    server.use(
      http.get('/api/v1/dashboard/metrics', () =>
        HttpResponse.json({
          code: 'OK',
          message: 'success',
          data: {
            from: '2026-07-01',
            to: '2026-07-18',
            granularity: 'day',
            summary: {
              active_users: 6,
              chat_messages: 42,
              work_orders: 3,
              posts: 8,
              lost_found_items: 2,
              moderation_pending: 1,
              llm_tokens: 9000,
            },
            series: {},
          },
          request_id: 'req-1',
          timestamp: '2026-07-18T00:00:00Z',
        }),
      ),
    )
    const store = useAuthStore()
    store.user = ADMIN as never
    store.status = 'authenticated'
    tokenStore.set('test-access-token')
    const router = makeRouter()
    await router.push('/')
    const wrapper = mount(DashboardView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('42'))
    expect(wrapper.text()).toContain('问答消息')
    expect(wrapper.text()).toContain('9000')
    tokenStore.clear()
  })

  it('shows permission-filtered quick entries for regular users', async () => {
    const store = useAuthStore()
    store.user = {
      ...ADMIN,
      display_name: '学生一',
      roles: [{ id: 'r1', code: 'student', name: '学生' }],
      permissions: ['chat:use', 'service:read'],
    } as never
    store.status = 'authenticated'
    const router = makeRouter()
    await router.push('/')
    const wrapper = mount(DashboardView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('知识库')
    )
    expect(wrapper.text()).not.toContain('用户管理')
  })
})
