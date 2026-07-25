import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, tokenStore } from '@/api/client'
import type { CurrentUser } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import WorkOrdersView from '@/modules/services/WorkOrdersView.vue'
import {
  describeCreateError,
  describeProgressError,
  describeRatingError,
  describeTransitionError,
  legalTransitions,
} from '@/modules/services/services-utils'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe('legalTransitions（与后端工单状态机一致）', () => {
  it('submitted 可受理/驳回/取消', () => {
    expect(legalTransitions('submitted').map((action) => action.target)).toEqual([
      'accepted',
      'rejected',
      'cancelled',
    ])
  })

  it('accepted 只能开始处理', () => {
    expect(legalTransitions('accepted').map((action) => action.target)).toEqual(['processing'])
  })

  it('processing 只能完成，且必须填写处理说明', () => {
    const actions = legalTransitions('processing')
    expect(actions.map((action) => action.target)).toEqual(['completed'])
    expect(actions[0]?.requiresCompletionNote).toBe(true)
  })

  it('终态没有合法流转', () => {
    for (const status of ['completed', 'cancelled', 'rejected'] as const) {
      expect(legalTransitions(status)).toEqual([])
    }
  })
})

describe('错误码映射', () => {
  it('重复评价映射为不能重复评价', () => {
    expect(describeRatingError(new ApiError(409, 'WORK_ORDER_ALREADY_RATED', 'x'))).toContain('重复评价')
  })

  it('未完成评价映射为完成后才能评价', () => {
    expect(describeRatingError(new ApiError(409, 'WORK_ORDER_NOT_COMPLETED', 'x'))).toContain('完成后才能评价')
  })

  it('版本冲突提示刷新后重试', () => {
    expect(describeTransitionError(new ApiError(409, 'RESOURCE_VERSION_CONFLICT', 'x'))).toContain('刷新')
  })

  it('非法状态流转单独映射', () => {
    expect(describeTransitionError(new ApiError(409, 'WORK_ORDER_ILLEGAL_TRANSITION', 'x'))).toContain('不允许')
  })

  it('创建工单 404 映射为校区不存在', () => {
    expect(describeCreateError(new ApiError(404, 'CAMPUS_NOT_FOUND', 'x'))).toContain('校区')
  })

  it('创建工单 422 使用 details 首条原因', () => {
    expect(
      describeCreateError(
        new ApiError(422, 'VALIDATION_ERROR', 'x', [{ field: 'description', reason: '描述至少 10 字' }]),
      ),
    ).toBe('描述至少 10 字')
  })

  it('外部进度超时与无记录分别映射', () => {
    expect(describeProgressError(new ApiError(503, 'CAMPUS_SYSTEM_TIMEOUT', 'x'))).toContain('超时')
    expect(describeProgressError(new ApiError(404, 'SERVICE_PROGRESS_NOT_FOUND', 'x'))).toContain('未查询到')
  })
})

/* ---------- 组件级：新建工单对话框重试复用同一幂等键 ---------- */

const USER: CurrentUser = {
  id: '3f0e6c8a-1111-4000-8000-000000000001',
  username: 'student01',
  display_name: '学生 01',
  status: 'active',
  roles: [],
  permissions: ['work_order:read', 'work_order:create'],
  created_at: '2026-07-01T00:00:00Z',
  version: 1,
}

function setNativeValue(element: Element | null, value: string): void {
  if (!element) {
    throw new Error('表单字段未渲染')
  }
  const field = element as HTMLInputElement
  field.value = value
  field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
}

describe('WorkOrdersView 新建工单', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    tokenStore.set('test-token')
    const auth = useAuthStore()
    auth.user = USER
    server.use(
      http.get('/api/v1/work-orders', () =>
        HttpResponse.json({
          code: 'OK',
          message: 'success',
          data: { items: [], pagination: { page: 1, page_size: 10, total: 0, total_pages: 0 } },
          request_id: 'r',
          timestamp: '',
        }),
      ),
      http.get('/api/v1/department-contacts', () =>
        HttpResponse.json({
          code: 'OK',
          message: 'success',
          data: {
            items: [
              {
                id: 'c1',
                department_id: 'd1',
                campus_code: 'wangjiang',
                office_name: '后勤服务大厅',
                location: '东园一舍一层',
                valid_from: '2026-01-01',
              },
            ],
          },
          request_id: 'r',
          timestamp: '',
        }),
      ),
    )
  })

  it('同一对话框会话内重试复用同一 Idempotency-Key', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.post('/api/v1/work-orders', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json(
          { code: 'IDEMPOTENCY_CONFLICT', message: '幂等键冲突', details: [], request_id: 'r', timestamp: '' },
          { status: 409 },
        )
      }),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/orders', name: 'work-orders-mine', component: WorkOrdersView },
        { path: '/orders/:workOrderId', name: 'work-order-detail', component: { template: '<p>detail</p>' } },
      ],
    })
    await router.push({ name: 'work-orders-mine' })
    const wrapper = mount(WorkOrdersView, {
      props: { mode: 'mine' },
      attachTo: document.body,
      global: { plugins: [router, ElementPlus] },
    })
    await vi.waitFor(() => expect(wrapper.text()).toContain('暂无工单'))
    await wrapper.get('[data-test="open-create"]').trigger('click')
    await vi.waitFor(() => expect(document.querySelector('#wo-campus')).not.toBeNull())

    setNativeValue(document.querySelector('#wo-campus'), 'wangjiang')
    setNativeValue(document.querySelector('#wo-area'), '东园一舍')
    setNativeValue(document.querySelector('#wo-building'), '3 栋')
    setNativeValue(document.querySelector('#wo-room'), '521')
    setNativeValue(document.querySelector('#wo-category'), 'electric')
    setNativeValue(document.querySelector('#wo-description'), '宿舍插座没电，需要维修')
    setNativeValue(document.querySelector('#wo-start'), '2026-07-20T10:00')
    setNativeValue(document.querySelector('#wo-end'), '2026-07-20T12:00')

    const form = document.querySelector('form[data-test="create-form"]')
    expect(form).not.toBeNull()
    form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(keys).toHaveLength(1))
    await vi.waitFor(() => expect(document.body.textContent).toContain('请勿重复提交'))

    form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBeTruthy()
    expect(keys[0]).toBe(keys[1])
    wrapper.unmount()
  })
})
