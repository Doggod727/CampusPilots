import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { tokenStore } from '@/api/client'
import type { Post, Topic } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import AnonymousRevealView from '@/modules/community/AnonymousRevealView.vue'
import PostDetailView from '@/modules/community/PostDetailView.vue'
import PostsView from '@/modules/community/PostsView.vue'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  document.body.innerHTML = ''
})
afterAll(() => server.close())

/* Element Plus 弹层组件在 jsdom 中需要 ResizeObserver。 */
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const TOPIC: Topic = {
  id: 'topic-1',
  code: 'general',
  name: '综合交流',
  description: null,
  allow_anonymous: true,
  sort_order: 0,
  status: 'active',
  version: 1,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

function makePost(overrides: Partial<Post> = {}): Post {
  return {
    id: 'post-1',
    topic: TOPIC,
    author: { user_id: 'user-1', display_name: '张三', avatar_url: null, is_anonymous: false },
    title: '食堂新窗口测评',
    content_markdown: '正文内容',
    is_anonymous: false,
    status: 'published',
    moderation_case_id: null,
    like_count: 3,
    favorite_count: 1,
    comment_count: 2,
    report_count: 0,
    interaction: { liked: false, favorited: false },
    published_at: '2026-07-10T08:00:00Z',
    version: 2,
    created_at: '2026-07-10T08:00:00Z',
    updated_at: '2026-07-10T08:00:00Z',
    ...overrides,
  }
}

const USER = {
  id: 'user-1',
  username: 'zhangsan',
  display_name: '张三',
  status: 'active',
  roles: [],
  permissions: ['community:read', 'community:write'],
  created_at: '2026-07-01T00:00:00Z',
  version: 1,
}

function signIn(permissions: string[] = USER.permissions) {
  const store = useAuthStore()
  store.user = { ...USER, permissions } as never
  store.status = 'authenticated'
  tokenStore.set('test-token')
}

function envelope(data: unknown, status = 200) {
  return HttpResponse.json(
    { code: 'OK', message: 'success', data, request_id: 'req-1', timestamp: '2026-07-18T00:00:00Z' },
    { status },
  )
}

function errorEnvelope(status: number, code: string, details: Array<{ field?: string; reason: string }> = []) {
  return HttpResponse.json(
    { code, message: '请求失败', details, request_id: 'req-err', timestamp: '2026-07-18T00:00:00Z' },
    { status },
  )
}

function pageOf<T>(items: T[], total = items.length) {
  return { items, pagination: { page: 1, page_size: 10, total, total_pages: Math.max(1, Math.ceil(total / 10)) } }
}

describe('PostsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    signIn()
  })

  function makeRouter() {
    return createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/community/posts', name: 'community-posts', component: PostsView },
        { path: '/community/posts/:postId', name: 'community-post-detail', component: { template: '<p>detail</p>' } },
      ],
    })
  }

  it('loads the list and forwards the route topicId filter', async () => {
    let seenTopic: string | null = null
    server.use(
      http.get('/api/v1/posts', ({ request }) => {
        seenTopic = new URL(request.url).searchParams.get('topic_id')
        return envelope(pageOf([makePost()]))
      }),
    )
    const router = makeRouter()
    await router.push({ name: 'community-posts', query: { topicId: 'topic-1' } })
    await router.isReady()
    const wrapper = mount(PostsView, { global: { plugins: [router, ElementPlus] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('食堂新窗口测评'))
    expect(seenTopic).toBe('topic-1')
    expect(wrapper.text()).toContain('综合交流')
    expect(wrapper.text()).toContain('张三')
  })

  it('shows edit/delete actions only on own posts', async () => {
    const other = makePost({
      id: 'post-2',
      title: '他人的帖子',
      author: { user_id: 'user-2', display_name: '李四', avatar_url: null, is_anonymous: false },
    })
    server.use(http.get('/api/v1/posts', () => envelope(pageOf([makePost(), other], 2))))
    const router = makeRouter()
    await router.push({ name: 'community-posts' })
    await router.isReady()
    const wrapper = mount(PostsView, { global: { plugins: [router, ElementPlus] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('他人的帖子'))
    expect(wrapper.findAll('.posts__actions')).toHaveLength(1)
  })

  it('keeps the same idempotency key when retrying a failed create', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.get('/api/v1/posts', () => envelope(pageOf([]))),
      http.get('/api/v1/topics', () => envelope(pageOf([TOPIC]))),
      http.post('/api/v1/posts', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return errorEnvelope(422, 'VALIDATION_FAILED', [{ field: 'title', reason: '标题包含敏感词' }])
      }),
    )
    const router = makeRouter()
    await router.push({ name: 'community-posts' })
    await router.isReady()
    const wrapper = mount(PostsView, { attachTo: document.body, global: { plugins: [router, ElementPlus] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('暂无帖子'))

    const openButton = wrapper.findAll('button').find((button) => button.text() === '发帖')
    expect(openButton).toBeTruthy()
    await openButton!.trigger('click')
    await vi.waitFor(() => expect(document.querySelector('.el-dialog form')).toBeTruthy())

    const dialog = document.querySelector('.el-dialog') as HTMLElement
    await vi.waitFor(() => expect(dialog.querySelector('#post-topic option[value="topic-1"]')).toBeTruthy())
    const topicSelect = dialog.querySelector('#post-topic') as HTMLSelectElement
    topicSelect.value = TOPIC.id
    topicSelect.dispatchEvent(new Event('change'))
    const titleInput = dialog.querySelector('#post-title') as HTMLInputElement
    titleInput.value = '测试标题'
    titleInput.dispatchEvent(new Event('input'))
    const contentInput = dialog.querySelector('#post-content') as HTMLTextAreaElement
    contentInput.value = '测试内容'
    contentInput.dispatchEvent(new Event('input'))
    await vi.waitFor(() => expect((dialog.querySelector('button[type="submit"]') as HTMLButtonElement).disabled).toBe(false))

    ;(dialog.querySelector('button[type="submit"]') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(dialog.textContent).toContain('标题包含敏感词'))
    ;(dialog.querySelector('button[type="submit"]') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBeTruthy()
    expect(keys[0]).toBe(keys[1])
  })
})

describe('PostDetailView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    signIn()
  })

  function makeRouter() {
    return createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/community/posts', name: 'community-posts', component: { template: '<p>list</p>' } },
        { path: '/community/posts/:postId', name: 'community-post-detail', component: PostDetailView },
      ],
    })
  }

  async function mountDetail(postId: string, attach = false) {
    const router = makeRouter()
    await router.push({ name: 'community-post-detail', params: { postId } })
    await router.isReady()
    return mount(PostDetailView, { ...(attach ? { attachTo: document.body } : {}), global: { plugins: [router, ElementPlus] } })
  }

  it('renders a safe empty state for invisible or missing posts', async () => {
    server.use(
      http.get('/api/v1/posts/post-missing', () => errorEnvelope(404, 'POST_NOT_FOUND')),
    )
    const wrapper = await mountDetail('post-missing')
    await vi.waitFor(() => expect(wrapper.text()).toContain('内容不存在或不可见'))
  })

  it('toggles a reaction and trusts the server counters', async () => {
    const calls: string[] = []
    server.use(
      http.get('/api/v1/posts/post-1', () => envelope(makePost())),
      http.get('/api/v1/posts/post-1/comments', () => envelope(pageOf([]))),
      http.put('/api/v1/posts/post-1/reactions/like', () => {
        calls.push('put')
        return envelope({ post_id: 'post-1', reaction_type: 'like', active: true, like_count: 4, favorite_count: 1 })
      }),
      http.delete('/api/v1/posts/post-1/reactions/like', () => {
        calls.push('delete')
        return envelope({ post_id: 'post-1', reaction_type: 'like', active: false, like_count: 3, favorite_count: 1 })
      }),
    )
    const wrapper = await mountDetail('post-1')
    await vi.waitFor(() => expect(wrapper.text()).toContain('食堂新窗口测评'))

    const likeButton = () => wrapper.findAll('.detail__reaction')[0]
    expect(likeButton().text()).toContain('点赞 3')
    await likeButton().trigger('click')
    await vi.waitFor(() => expect(likeButton().text()).toContain('已点赞 4'))
    await likeButton().trigger('click')
    await vi.waitFor(() => expect(likeButton().text()).toContain('点赞 3'))
    expect(calls).toEqual(['put', 'delete'])
  })

  it('submits a report with a fixed idempotency key across retries', async () => {
    const keys: (string | null)[] = []
    const bodies: Array<{ target_type?: string; target_id?: string; reason_code?: string; details?: string }> = []
    let attempts = 0
    server.use(
      http.get('/api/v1/posts/post-1', () => envelope(makePost())),
      http.get('/api/v1/posts/post-1/comments', () => envelope(pageOf([]))),
      http.post('/api/v1/reports', async ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        bodies.push((await request.json()) as (typeof bodies)[number])
        attempts += 1
        if (attempts === 1) {
          return errorEnvelope(429, 'RATE_LIMITED')
        }
        return envelope(
          {
            id: 'report-1',
            target_type: 'post',
            target_id: 'post-1',
            reason_code: 'spam',
            status: 'submitted',
            moderation_case_id: null,
            created_at: '2026-07-18T00:00:00Z',
          },
          201,
        )
      }),
    )
    const wrapper = await mountDetail('post-1', true)
    await vi.waitFor(() => expect(wrapper.text()).toContain('食堂新窗口测评'))

    const reportButton = wrapper.findAll('button').find((button) => button.text() === '举报')
    expect(reportButton).toBeTruthy()
    await reportButton!.trigger('click')
    await vi.waitFor(() => expect(document.querySelector('.el-dialog form')).toBeTruthy())

    const dialog = document.querySelector('.el-dialog') as HTMLElement
    const submitButton = () => dialog.querySelector('button[type="submit"]') as HTMLButtonElement
    expect(submitButton().disabled).toBe(true)

    const detailsInput = dialog.querySelector('#report-details') as HTMLTextAreaElement
    detailsInput.value = '该帖疑似广告刷屏'
    detailsInput.dispatchEvent(new Event('input'))
    await vi.waitFor(() => expect(submitButton().disabled).toBe(false))

    submitButton().click()
    await vi.waitFor(() => expect(dialog.textContent).toContain('操作过于频繁'))
    submitButton().click()
    await vi.waitFor(() => expect(wrapper.text()).toContain('举报已提交'))

    expect(keys).toHaveLength(2)
    expect(keys[0]).toBeTruthy()
    expect(keys[0]).toBe(keys[1])
    expect(bodies[0]).toMatchObject({ target_type: 'post', target_id: 'post-1', reason_code: 'spam', details: '该帖疑似广告刷屏' })
  })
})

describe('AnonymousRevealView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('blocks users without the dedicated permission', () => {
    signIn(['community:read'])
    const wrapper = mount(AnonymousRevealView)
    expect(wrapper.text()).toContain('权限不足')
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('reveals an identity, shows the audit notice and keeps memory only', async () => {
    signIn(['community:read', 'community:anonymous_identity:read'])
    let seenBody: { target_type?: string; target_id?: string; reason?: string } = {}
    server.use(
      http.post('/api/v1/community/anonymous-identities/reveal', async ({ request }) => {
        seenBody = (await request.json()) as typeof seenBody
        return envelope({
          target_type: 'post',
          target_id: 'post-1',
          author_user_id: 'user-9',
          username: 'hidden01',
          display_name: '隐藏用户',
          reason: '举报案件核实需要',
          revealed_at: '2026-07-18T01:00:00Z',
        })
      }),
    )
    const wrapper = mount(AnonymousRevealView)
    await wrapper.find('#reveal-target').setValue('post-1')
    await wrapper.find('#reveal-reason').setValue('举报案件核实需要')
    await wrapper.find('form').trigger('submit.prevent')

    await vi.waitFor(() => expect(wrapper.text()).toContain('隐藏用户'))
    expect(wrapper.text()).toContain('user-9')
    expect(wrapper.text()).toContain('本次操作已记录审计')
    expect(seenBody).toMatchObject({ target_type: 'post', target_id: 'post-1', reason: '举报案件核实需要' })
    expect(window.localStorage.length).toBe(0)
    wrapper.unmount()
  })
})
