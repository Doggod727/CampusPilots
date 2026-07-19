import { mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { tokenStore } from '@/api/client'
import RunCreateView from '@/modules/agent-workbench/RunCreateView.vue'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/agent/runs', name: 'agent-runs', component: RunCreateView },
      { path: '/agent/runs/:runId', name: 'agent-run-detail', component: { template: '<p>detail</p>' } },
    ],
  })
}

describe('RunCreateView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    tokenStore.set('test-token')
    server.use(
      http.get('/api/v1/agents', () => HttpResponse.json({ code: 'OK', message: 'success', data: { items: [] }, request_id: 'r', timestamp: '' })),
      http.get('/api/v1/tools', () => HttpResponse.json({ code: 'OK', message: 'success', data: { items: [] }, request_id: 'r', timestamp: '' })),
    )
  })

  it('creates a run and navigates to its detail', async () => {
    server.use(
      http.post('/api/v1/agent-runs', () =>
        HttpResponse.json({
          code: 'OK',
          message: 'success',
          data: { id: 'run-1', status: 'created', route: null, router_model: null, router_confidence: null, input_summary: 'x', final_answer: null, error_code: null, created_at: '', updated_at: '', finished_at: null },
          request_id: 'r1',
          timestamp: '',
        }, { status: 202 }),
      ),
    )
    const router = makeRouter()
    await router.push({ name: 'agent-runs' })
    const wrapper = mount(RunCreateView, { global: { plugins: [router] } })
    await wrapper.find('textarea').setValue('查询校区地址')
    await wrapper.find('form').trigger('submit.prevent')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('agent-run-detail'))
    expect(router.currentRoute.value.params.runId).toBe('run-1')
  })

  it('shows the conflict state and reuses the same idempotency key on retry', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.post('/api/v1/agent-runs', async ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json(
          { code: 'AGENT_DISABLED', message: 'Agent 已停用', details: [], request_id: 'r2', timestamp: '' },
          { status: 409 },
        )
      }),
    )
    const router = makeRouter()
    await router.push({ name: 'agent-runs' })
    const wrapper = mount(RunCreateView, { global: { plugins: [router] } })
    await wrapper.find('textarea').setValue('测试任务')
    await wrapper.find('form').trigger('submit.prevent')
    await vi.waitFor(() => expect(wrapper.text()).toContain('创建冲突'))
    await wrapper.find('form').trigger('submit.prevent')
    await vi.waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBe(keys[1])
  })
})
