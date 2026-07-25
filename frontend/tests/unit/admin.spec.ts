import { mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, tokenStore } from '@/api/client'
import { describeApiError } from '@/modules/admin/admin-utils'
import ConfigView from '@/modules/admin/ConfigView.vue'
import JsonTree from '@/modules/admin/JsonTree.vue'
import UsersView from '@/modules/admin/UsersView.vue'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function envelope(data: unknown, status = 200) {
  return HttpResponse.json({ code: 'OK', message: 'success', data, request_id: 'r', timestamp: '' }, { status })
}

function errorEnvelope(status: number, code: string, message: string) {
  return HttpResponse.json({ code, message, details: [], request_id: 'r', timestamp: '' }, { status })
}

describe('describeApiError', () => {
  it('优先使用稳定错误码映射', () => {
    const failure = describeApiError(new ApiError(409, 'DUPLICATE_RESOURCE', '用户名已存在'), '创建用户失败', {
      DUPLICATE_RESOURCE: '用户名已存在，请更换后重试。',
    })
    expect(failure.title).toBe('创建用户失败')
    expect(failure.message).toBe('用户名已存在，请更换后重试。')
  })

  it('映射最后超级管理员保护', () => {
    const failure = describeApiError(new ApiError(409, 'LAST_SUPER_ADMIN', 'x'), '状态变更失败', {
      LAST_SUPER_ADMIN: '不能停用最后一个有效超级管理员。',
    })
    expect(failure.message).toBe('不能停用最后一个有效超级管理员。')
  })

  it('未命中映射的 409 走通用冲突文案', () => {
    const failure = describeApiError(new ApiError(409, 'RESOURCE_VERSION_CONFLICT', 'x'), '保存失败')
    expect(failure.title).toBe('操作冲突')
  })

  it('422 展示首条字段原因', () => {
    const failure = describeApiError(
      new ApiError(422, 'VALIDATION_FAILED', 'x', [{ field: 'reason', reason: '处理理由至少 2 个字' }]),
      '提交决定失败',
    )
    expect(failure.title).toBe('输入无效')
    expect(failure.message).toBe('处理理由至少 2 个字')
  })

  it('非 ApiError 走兜底文案', () => {
    const failure = describeApiError(new Error('boom'), '保存配置失败')
    expect(failure.message).toBe('服务暂不可用，请稍后重试。')
  })
})

describe('JsonTree', () => {
  it('递归展示 JSON 并掩码疑似密钥字段', () => {
    const wrapper = mount(JsonTree, {
      props: {
        label: 'after_data',
        value: { username: 'alice', password: 'p@ss-value-123', nested: { access_token: 'token-value-456' } },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('alice')
    expect(text).not.toContain('p@ss-value-123')
    expect(text).not.toContain('token-value-456')
    expect(text).toContain('***（已掩码）')
  })
})

describe('ConfigView', () => {
  const editableConfig = {
    key: 'feature.chat.enabled',
    namespace: 'feature',
    value: true,
    value_type: 'boolean',
    description: '聊天开关',
    editable: true,
    version: 3,
    updated_at: '2026-07-01T00:00:00Z',
    updated_by: 'admin',
  }
  const readonlyConfig = {
    key: 'security.jwt.ttl',
    namespace: 'security',
    value: '***',
    value_type: 'string',
    description: null,
    editable: false,
    version: 1,
    updated_at: '2026-07-01T00:00:00Z',
    updated_by: null,
  }

  beforeEach(() => tokenStore.set('test-token'))

  it('仅 editable 配置提供编辑入口，只读项禁用提交', async () => {
    server.use(http.get('/api/v1/configs', () => envelope({ items: [editableConfig, readonlyConfig] })))
    const wrapper = mount(ConfigView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('feature.chat.enabled'))

    const editButtons = wrapper.findAll('button').filter((button) => button.text() === '编辑')
    expect(editButtons).toHaveLength(1)
    expect(wrapper.text()).toContain('只读')
    expect(wrapper.text()).toContain('security.jwt.ttl')
  })

  it('编辑可修改配置：按 value_type 解析并携带 version', async () => {
    const bodies: unknown[] = []
    server.use(
      http.get('/api/v1/configs', () => envelope({ items: [editableConfig] })),
      http.patch('/api/v1/configs/feature.chat.enabled', async ({ request }) => {
        bodies.push(await request.json())
        return envelope({ ...editableConfig, value: false, version: 4 })
      }),
    )
    const wrapper = mount(ConfigView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('feature.chat.enabled'))

    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    await wrapper.find('select').setValue('false')
    await wrapper.find('form').trigger('submit.prevent')

    await vi.waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ value: false, version: 3 })
    await vi.waitFor(() => expect(wrapper.text()).toContain('已保存'))
  })

  it('版本冲突时展示稳定错误码对应文案', async () => {
    server.use(
      http.get('/api/v1/configs', () => envelope({ items: [editableConfig] })),
      http.patch('/api/v1/configs/feature.chat.enabled', () =>
        errorEnvelope(409, 'RESOURCE_VERSION_CONFLICT', '数据已被其他操作更新，请刷新后重试'),
      ),
    )
    const wrapper = mount(ConfigView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('feature.chat.enabled'))

    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    await wrapper.find('select').setValue('false')
    await wrapper.find('form').trigger('submit.prevent')

    await vi.waitFor(() => expect(wrapper.text()).toContain('数据已被其他操作更新，请刷新列表后重试。'))
  })
})

describe('UsersView', () => {
  const role = {
    id: 'role-1',
    code: 'student',
    name: '学生',
    description: null,
    is_system: false,
    permissions: [],
    user_count: 0,
    created_at: '2026-07-01T00:00:00Z',
    version: 1,
  }

  beforeEach(() => {
    tokenStore.set('test-token')
    server.use(
      http.get('/api/v1/users', () =>
        envelope({ items: [], pagination: { page: 1, page_size: 10, total: 0, total_pages: 0 } }),
      ),
      http.get('/api/v1/roles', () => envelope({ items: [role] })),
    )
  })

  it('重复用户名返回 409 时展示稳定错误码文案，且重试复用同一幂等键', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.post('/api/v1/users', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return errorEnvelope(409, 'DUPLICATE_RESOURCE', '用户名已存在')
      }),
    )
    const wrapper = mount(UsersView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('新建用户'))

    await wrapper.findAll('button').find((button) => button.text() === '新建用户')!.trigger('click')
    await vi.waitFor(() => expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true))
    await wrapper.find('#user-create-username').setValue('newuser')
    await wrapper.find('#user-create-password').setValue('1234567890')
    await wrapper.find('#user-create-display').setValue('新用户')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('form').trigger('submit.prevent')

    await vi.waitFor(() => expect(wrapper.text()).toContain('用户名已存在，请更换后重试。'))

    await wrapper.find('form').trigger('submit.prevent')
    await vi.waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBeTruthy()
    expect(keys[0]).toBe(keys[1])
  })
})
