import { mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { tokenStore } from '@/api/client'
import type { Approval } from '@/api/generated'
import ApprovalCards from '@/modules/agent-workbench/ApprovalCards.vue'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const RUN_ID = 'a025cb9d-1970-40e1-8f4f-05a8d62d694e'
const APPROVAL: Approval = {
  id: '322d499d-7ea0-4022-a09d-ba0baf66e3fc',
  run_id: RUN_ID,
  tool_name: 'electricity.create_topup_request',
  argument_summary: {},
  argument_hash: 'a'.repeat(64),
  status: 'pending',
  expires_at: new Date(Date.now() + 600_000).toISOString(),
  created_at: new Date().toISOString(),
}

describe('ApprovalCards', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    tokenStore.set('test-token')
  })

  it('posts the decision with argument hash and emits decided', async () => {
    let seen: { hash?: string; decision?: string } = {}
    server.use(
      http.post(`/api/v1/agent-runs/${RUN_ID}/approvals/${APPROVAL.id}`, async ({ request }) => {
        const body = (await request.json()) as { decision: string; argument_hash: string }
        seen = { hash: body.argument_hash, decision: body.decision }
        return HttpResponse.json({ code: 'OK', message: 'success', data: APPROVAL, request_id: 'r', timestamp: '' })
      }),
    )
    const wrapper = mount(ApprovalCards, { props: { runId: RUN_ID, approvals: [APPROVAL] } })
    expect(wrapper.text()).toContain('是否允许 CampusPilot 为您提交电费充值申请？')
    expect(wrapper.text()).toContain('查看技术信息')
    await wrapper.get('button').trigger('click')
    await vi.waitFor(() => expect(seen.decision).toBe('approve'))
    expect(seen.hash).toBe('a'.repeat(64))
    expect(wrapper.text()).toContain('已批准')
    expect(wrapper.emitted('decided')).toBeTruthy()
  })

  it('shows one-time consumption notice on 409', async () => {
    server.use(
      http.post(`/api/v1/agent-runs/${RUN_ID}/approvals/${APPROVAL.id}`, () =>
        HttpResponse.json(
          { code: 'TOOL_APPROVAL_INVALID', message: '审批无效或已消费', details: [], request_id: 'r', timestamp: '' },
          { status: 409 },
        ),
      ),
    )
    const wrapper = mount(ApprovalCards, { props: { runId: RUN_ID, approvals: [APPROVAL] } })
    await wrapper.get('button').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('一次性消费'))
  })
})
