import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, tokenStore } from '@/api/client'
import type { ToolCatalogItem } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import ToolCatalogView from '@/modules/modelops/ToolCatalogView.vue'
import { describeModelOpsError } from '@/modules/modelops/errors'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const TOOL: ToolCatalogItem = {
  name: 'knowledge.search',
  module: 'm1',
  description: '检索知识库',
  risk_level: 'r1',
  enabled: true,
  version: '1.0.0',
  input_schema: { type: 'object' },
  output_schema: { type: 'object' },
  required_permissions: ['knowledge:read'],
  timeout_ms: 5000,
  idempotent: true,
  requires_approval: false,
}

describe('describeModelOpsError', () => {
  it('maps stable backend error codes to Chinese messages', () => {
    expect(describeModelOpsError(new ApiError(409, 'DATASET_IN_USE', '数据集正在被训练任务使用'), 'x')).toContain(
      '活动训练任务引用',
    )
    expect(describeModelOpsError(new ApiError(409, 'MODEL_EVALUATION_REQUIRED', '模型尚未通过评估'), 'x')).toContain(
      '不允许激活',
    )
    expect(describeModelOpsError(new ApiError(415, 'DATASET_ARTIFACT_UNSUPPORTED', '仅支持 JSONL 或 CSV 数据集'), 'x')).toContain(
      'JSONL 或 CSV',
    )
    expect(describeModelOpsError(new ApiError(413, 'DATASET_ARTIFACT_TOO_LARGE', '数据集文件超过大小限制'), 'x')).toContain(
      '100 MiB',
    )
  })

  it('falls back to 422 details, 403 message and generic fallback', () => {
    expect(
      describeModelOpsError(new ApiError(422, 'VALIDATION_FAILED', '无效', [{ field: 'name', reason: '名称过短' }]), 'x'),
    ).toBe('名称过短')
    expect(describeModelOpsError(new ApiError(403, 'FORBIDDEN', '无权限'), 'x')).toContain('权限')
    expect(describeModelOpsError(new Error('network'), '服务暂不可用')).toBe('服务暂不可用')
  })
})

describe('ToolCatalogView 启停', () => {
  beforeEach(() => {
    const pinia = createPinia()
    setActivePinia(pinia)
    tokenStore.set('test-token')
    const auth = useAuthStore()
    auth.user = {
      id: 'u1',
      username: 'admin',
      display_name: '管理员',
      status: 'active',
      roles: [],
      created_at: '',
      version: 1,
      permissions: ['tool:catalog:read', 'tool:catalog:write'],
    }
    server.use(
      http.get('/api/v1/tools', () =>
        HttpResponse.json({ code: 'OK', message: 'success', data: { items: [TOOL] }, request_id: 'r', timestamp: '' }),
      ),
    )
  })

  function dialogButton(text: string): HTMLButtonElement {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === text)
    if (!button) {
      throw new Error(`button not found: ${text}`)
    }
    return button as HTMLButtonElement
  }

  async function openToggleDialog(wrapper: ReturnType<typeof mount>) {
    await wrapper.findAll('button').find((item) => item.text() === '停用')!.trigger('click')
    await vi.waitFor(() => expect(document.querySelector('#toggle-reason')).toBeTruthy())
    const textarea = document.querySelector('#toggle-reason') as HTMLTextAreaElement
    textarea.value = '上游维护，临时停用'
    textarea.dispatchEvent(new Event('input'))
  }

  it('reuses the idempotency key across retries of one dialog session and regenerates on reopen', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.patch('/api/v1/tools/knowledge.search', async ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json(
          { code: 'TOOL_STATE_CONFIRMATION_REQUIRED', message: 'Tool 状态变更需要明确确认', details: [], request_id: 'r', timestamp: '' },
          { status: 409 },
        )
      }),
    )
    const wrapper = mount(ToolCatalogView, { attachTo: document.body, global: { plugins: [ElementPlus] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('knowledge.search'))

    await openToggleDialog(wrapper)
    dialogButton('确认停用').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('需要明确确认'))
    dialogButton('确认停用').click()
    await vi.waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBeTruthy()
    expect(keys[0]).toBe(keys[1])

    dialogButton('取消').click()
    await vi.waitFor(() => {
      const overlay = document.querySelector('.el-overlay') as HTMLElement | null
      expect(!overlay || overlay.style.display === 'none').toBe(true)
    })
    await openToggleDialog(wrapper)
    dialogButton('确认停用').click()
    await vi.waitFor(() => expect(keys).toHaveLength(3))
    expect(keys[2]).toBeTruthy()
    expect(keys[2]).not.toBe(keys[0])
  })

  it('hides the toggle action without tool:catalog:write permission', async () => {
    const auth = useAuthStore()
    auth.user = { ...auth.user!, permissions: ['tool:catalog:read'] }
    const wrapper = mount(ToolCatalogView, { global: { plugins: [ElementPlus] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('knowledge.search'))
    expect(wrapper.findAll('button').some((item) => item.text() === '停用')).toBe(false)
    expect(wrapper.findAll('button').some((item) => item.text() === '详情')).toBe(true)
  })
})
