import { mount, type VueWrapper } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { tokenStore } from '@/api/client'
import { useAuthStore } from '@/modules/auth/stores/auth'
import EventsView from '@/modules/community/EventsView.vue'

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

function makeEvent(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ev-1',
    organizer: { user_id: 'user-2', display_name: '社团负责人', avatar_url: null, is_anonymous: false },
    title: '迎新晚会',
    description_markdown: '欢迎新同学',
    category: '文艺',
    location: '学生活动中心',
    starts_at: '2026-09-01T11:00:00.000Z',
    ends_at: '2026-09-01T13:00:00.000Z',
    registration_deadline: '2026-08-30T12:00:00.000Z',
    capacity: 100,
    registered_count: 42,
    status: 'published',
    my_registration_status: null,
    cancellation_reason: null,
    moderation_case_id: null,
    published_at: '2026-08-01T00:00:00.000Z',
    version: 3,
    created_at: '2026-08-01T00:00:00.000Z',
    updated_at: '2026-08-01T00:00:00.000Z',
    ...overrides,
  }
}

function pageOf(items: unknown[], total = items.length) {
  return { items, pagination: { page: 1, page_size: 10, total, total_pages: Math.max(1, Math.ceil(total / 10)) } }
}

function findButton(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text().includes(text))
  expect(button, `按钮「${text}」应存在`).toBeTruthy()
  return button!
}

describe('EventsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    tokenStore.set('test-token')
    const auth = useAuthStore()
    auth.user = {
      id: 'user-1',
      username: 'student',
      display_name: '学生甲',
      email: null,
      department: null,
      status: 'active',
      roles: [],
      last_login_at: null,
      created_at: '',
      version: 1,
      permissions: ['community:read', 'community:write'],
    }
    auth.status = 'authenticated'
  })

  it('loads the event list and shows capacity/full state', async () => {
    server.use(
      http.get('/api/v1/events', () =>
        envelope(
          pageOf([
            makeEvent(),
            makeEvent({ id: 'ev-2', title: '篮球联赛', registered_count: 100, capacity: 100 }),
          ]),
        ),
      ),
    )
    const wrapper = mount(EventsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('迎新晚会'))
    expect(wrapper.text()).toContain('篮球联赛')
    expect(wrapper.text()).toContain('名额已满')
    expect(wrapper.text()).toContain('42/100')
  })

  it('applies filters as query parameters', async () => {
    const seen: string[] = []
    server.use(
      http.get('/api/v1/events', ({ request }) => {
        seen.push(request.url)
        return HttpResponse.json(
          { code: 'OK', message: 'success', data: pageOf([]), request_id: 'r', timestamp: '' },
        )
      }),
    )
    const wrapper = mount(EventsView)
    await vi.waitFor(() => expect(seen).toHaveLength(1))
    await wrapper.find('#event-filter-category').setValue('讲座')
    await wrapper.find('#event-filter-from').setValue('2026-09-01')
    await findButton(wrapper, '筛选').trigger('click')
    await vi.waitFor(() => expect(seen).toHaveLength(2))
    const url = new URL(seen[1])
    expect(url.searchParams.get('category')).toBe('讲座')
    expect(url.searchParams.get('starts_from')).toBe(new Date('2026-09-01T00:00:00').toISOString())
  })

  it('creates an event with an idempotency key and reloads the list', async () => {
    let listCalls = 0
    let createdBody: Record<string, unknown> | null = null
    let idempotencyKey: string | null = null
    server.use(
      http.get('/api/v1/events', () => {
        listCalls += 1
        return envelope(pageOf([makeEvent()]))
      }),
      http.post('/api/v1/events', async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>
        idempotencyKey = request.headers.get('Idempotency-Key')
        return envelope(makeEvent({ id: 'ev-new', title: '编程马拉松' }), 201)
      }),
    )
    const wrapper = mount(EventsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('迎新晚会'))
    await findButton(wrapper, '发布活动').trigger('click')
    await wrapper.find('#event-form-title').setValue('编程马拉松')
    await wrapper.find('#event-form-category').setValue('科技')
    await wrapper.find('#event-form-location').setValue('实验楼 A 区')
    await wrapper.find('#event-form-capacity').setValue('80')
    await wrapper.find('#event-form-starts').setValue('2026-10-01T09:00')
    await wrapper.find('#event-form-ends').setValue('2026-10-01T18:00')
    await wrapper.find('#event-form-deadline').setValue('2026-09-28T18:00')
    await wrapper.find('#event-form-description').setValue('24 小时编程挑战')
    await wrapper.find('form').trigger('submit.prevent')
    await vi.waitFor(() => expect(createdBody).not.toBeNull())
    expect(createdBody).toMatchObject({ title: '编程马拉松', capacity: 80, category: '科技' })
    expect(idempotencyKey).toBeTruthy()
    await vi.waitFor(() => expect(wrapper.find('#event-form-title').exists()).toBe(false))
    expect(listCalls).toBe(2)
  })

  it('maps EVENT_CAPACITY_FULL on register and reuses the idempotency key on retry', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.get('/api/v1/events', () => envelope(pageOf([makeEvent()]))),
      http.get('/api/v1/events/ev-1', () => envelope(makeEvent())),
      http.post('/api/v1/events/ev-1/registrations', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return errorEnvelope(409, 'EVENT_CAPACITY_FULL', '活动名额已满')
      }),
    )
    const wrapper = mount(EventsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('迎新晚会'))
    await wrapper.find('.events__item').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('立即报名'))
    await findButton(wrapper, '立即报名').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('名额已满'))
    await findButton(wrapper, '立即报名').trigger('click')
    await vi.waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBeTruthy()
    expect(keys[0]).toBe(keys[1])
  })

  it('maps EVENT_REGISTRATION_CLOSED when cancelling after start', async () => {
    server.use(
      http.get('/api/v1/events', () => envelope(pageOf([makeEvent({ my_registration_status: 'registered' })]))),
      http.get('/api/v1/events/ev-1', () => envelope(makeEvent({ my_registration_status: 'registered' }))),
      http.delete('/api/v1/events/ev-1/registrations/me', () =>
        errorEnvelope(409, 'EVENT_REGISTRATION_CLOSED', '活动报名已关闭'),
      ),
    )
    const wrapper = mount(EventsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('迎新晚会'))
    await wrapper.find('.events__item').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('取消报名'))
    await findButton(wrapper, '取消报名').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('报名已关闭'))
  })

  it('maps RESOURCE_VERSION_CONFLICT when updating with a stale version', async () => {
    server.use(
      http.get('/api/v1/events', () => envelope(pageOf([makeEvent({ organizer: { user_id: 'user-1', display_name: '学生甲', avatar_url: null, is_anonymous: false } })]))),
      http.get('/api/v1/events/ev-1', () =>
        envelope(makeEvent({ organizer: { user_id: 'user-1', display_name: '学生甲', avatar_url: null, is_anonymous: false } })),
      ),
      http.patch('/api/v1/events/ev-1', () => errorEnvelope(409, 'RESOURCE_VERSION_CONFLICT', '资源版本冲突')),
    )
    const wrapper = mount(EventsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('迎新晚会'))
    await wrapper.find('.events__item').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('编辑活动'))
    await findButton(wrapper, '编辑活动').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('#event-form-title').exists()).toBe(true))
    await wrapper.find('form').trigger('submit.prevent')
    await vi.waitFor(() => expect(wrapper.text()).toContain('版本冲突'))
  })
})
