import { mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { tokenStore } from '@/api/client'
import { useAuthStore } from '@/modules/auth/stores/auth'
import ChatView from '@/modules/chat-kb/ChatView.vue'
import IngestionView from '@/modules/chat-kb/IngestionView.vue'
import KnowledgeBasesView from '@/modules/chat-kb/KnowledgeBasesView.vue'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const NOW = '2026-07-18T00:00:00Z'

const envelope = (data: unknown) => ({
  code: 'OK',
  message: 'success',
  data,
  request_id: 'r',
  timestamp: NOW,
})
const errorEnvelope = (code: string, message: string) => ({
  code,
  message,
  details: [],
  request_id: 'r',
  timestamp: NOW,
})
const pageOf = (items: unknown[], total = items.length) => ({
  items,
  pagination: { page: 1, page_size: 20, total, total_pages: Math.max(1, Math.ceil(total / 20)) },
})

const KB = {
  id: 'kb-1',
  name: '校规知识库',
  description: null,
  visibility: 'private',
  owner_user_id: null,
  owner_department: null,
  embedding_model: 'bge-small-zh-v1.5',
  chunk_size: 512,
  chunk_overlap: 64,
  collection_name: 'kb_kb1',
  document_count: 0,
  members: [],
  created_by: 'u1',
  created_at: NOW,
  updated_at: NOW,
  version: 1,
}

const CONVERSATION = {
  id: 'conv-1',
  title: '校区咨询',
  status: 'active',
  message_count: 0,
  last_message_at: null,
  created_at: NOW,
  updated_at: NOW,
}

const SERVICE_AGENT = {
  code: 'service_agent',
  name: '校园服务 Agent',
  description: '办事指南、工单与电费',
  version: '1.0.0',
  enabled: true,
  tool_allowlist: ['electricity.get_balance'],
}

const ELECTRICITY_TOOL = {
  name: 'electricity.get_balance',
  module: 'm2',
  description: '查询本人绑定房间电费',
  risk_level: 'r0',
  enabled: true,
  version: '1.0.0',
  input_schema: {},
  output_schema: {},
  required_permissions: ['electricity:read_own'],
  timeout_ms: 3000,
  idempotent: true,
  requires_approval: false,
}

const CITATION = {
  citation_no: 1,
  chunk_id: 'chk-1',
  document_id: 'doc-1',
  document_title: '校区服务指南',
  source_location: '第一章 校区分布',
  page_number: 3,
  quote_excerpt: '望江校区位于成都市武侯区一环路南一段24号。',
  relevance_score: 0.87,
  preview_url: '',
}

function makeMessage(overrides: Record<string, unknown>) {
  return {
    id: 'm1',
    conversation_id: 'conv-1',
    sequence_no: 2,
    role: 'assistant',
    status: 'completed',
    content: '',
    request_id: 'r1',
    citations: [],
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

function sse(text: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/knowledge/bases', name: 'knowledge-bases', component: KnowledgeBasesView },
      { path: '/knowledge/ingestion', name: 'knowledge-ingestion', component: IngestionView },
    ],
  })
}

async function clickButton(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  expect(button, `应存在按钮「${text}」`).toBeTruthy()
  await button?.trigger('click')
}

function mountChat() {
  const EmptyRoute = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'chat', component: EmptyRoute },
      { path: '/login', name: 'login', component: EmptyRoute },
      { path: '/services/work-orders', name: 'work-orders-mine', component: EmptyRoute },
      { path: '/community/events', name: 'community-events', component: EmptyRoute },
      { path: '/community/claims', name: 'lost-found-claims', component: EmptyRoute },
    ],
  })
  return mount(ChatView, {
    global: {
      plugins: [router],
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  tokenStore.set('test-token')
})

describe('KnowledgeBasesView', () => {
  it('删除被占用知识库时映射 409 KNOWLEDGE_BASE_IN_USE', async () => {
    server.use(
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.delete('/api/v1/knowledge-bases/kb-1', () =>
        HttpResponse.json(errorEnvelope('KNOWLEDGE_BASE_IN_USE', '知识库仍被文档或会话引用'), {
          status: 409,
        }),
      ),
    )
    const router = makeRouter()
    await router.push({ name: 'knowledge-bases' })
    const wrapper = mount(KnowledgeBasesView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('校规知识库'))
    await clickButton(wrapper, '删除')
    await clickButton(wrapper, '确认删除')
    await vi.waitFor(() => expect(wrapper.text()).toContain('知识库仍被占用'))
  })
})

describe('IngestionView', () => {
  it('重复文件上传映射 409 DOCUMENT_ALREADY_EXISTS', async () => {
    server.use(
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/knowledge-bases/kb-1/documents', () =>
        HttpResponse.json(envelope(pageOf([]))),
      ),
      http.post('/api/v1/knowledge-bases/kb-1/documents', () =>
        HttpResponse.json(errorEnvelope('DOCUMENT_ALREADY_EXISTS', '文档已存在'), { status: 409 }),
      ),
    )
    const router = makeRouter()
    await router.push({ name: 'knowledge-ingestion' })
    const wrapper = mount(IngestionView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.find('#kb-select').exists()).toBe(true))
    await clickButton(wrapper, '上传文档')
    const file = new File(['%PDF-1.4 demo'], '校规汇编.pdf', { type: 'application/pdf' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await wrapper.find('.ingest__panel form').trigger('submit.prevent')
    await vi.waitFor(() => expect(wrapper.text()).toContain('相同内容的文件已上传过'))
  })
})

describe('ChatView', () => {
  function useChatHandlers(message: Record<string, unknown>, streamBody: string) {
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/conversations/conv-1/messages', () =>
        HttpResponse.json(envelope(pageOf([]))),
      ),
      http.post(
        '/api/v1/chat/stream',
        () => new Response(sse(streamBody), { headers: { 'Content-Type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/messages/${message.id as string}`, () =>
        HttpResponse.json(envelope(message)),
      ),
    )
  }

  async function ask(wrapper: ReturnType<typeof mount>, question: string) {
    await wrapper.find('.chat__conversation').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('暂无消息'))
    await wrapper.find('textarea.chat__input').setValue(question)
    await clickButton(wrapper, '发送')
  }

  async function askWithLibrary(wrapper: ReturnType<typeof mount>, question: string) {
    await wrapper.find('.chat__conversation').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('暂无消息'))
    const input = wrapper.find('textarea.chat__input')
    await input.setValue('/lib')
    await input.trigger('keydown', { key: 'Enter' })
    await input.setValue(question)
    await clickButton(wrapper, '发送')
  }

  it('shows permission-filtered modules as compact expandable navigation', async () => {
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/agents', () => HttpResponse.json(envelope({ items: [] }))),
      http.get('/api/v1/tools', () => HttpResponse.json(envelope({ items: [] }))),
    )
    const auth = useAuthStore()
    auth.user = {
      id: 'u1',
      username: 'student01',
      display_name: '张同学',
      status: 'active',
      roles: [{ id: 'r1', code: 'student', name: '普通学生' }],
      permissions: [
        'chat:use',
        'service:read',
        'work_order:read',
        'agent:run',
        'agent:catalog:read',
        'tool:catalog:read',
      ],
      created_at: NOW,
      version: 1,
    } as never
    auth.status = 'authenticated'

    const wrapper = mountChat()
    await vi.waitFor(() => expect(wrapper.text()).toContain('校园服务'))

    const campusTrigger = wrapper
      .findAll('.chat__module-trigger')
      .find((candidate) => candidate.text().includes('校园服务'))
    await campusTrigger?.trigger('click')
    expect(wrapper.find('.chat__module-links').text()).toContain('办事指南')
    expect(wrapper.find('.chat__module-links').text()).toContain('我的工单')
    expect(wrapper.text()).not.toContain('能力中心')
    expect(wrapper.text()).not.toContain('可用 Agent')
    expect(wrapper.text()).not.toContain('可用工具')
    expect(wrapper.text()).not.toContain('管理')
    wrapper.unmount()
  })

  it('opens a database-backed account menu without inventing a global approval page', async () => {
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/agents', () => HttpResponse.json(envelope({ items: [] }))),
      http.get('/api/v1/tools', () => HttpResponse.json(envelope({ items: [] }))),
    )
    const auth = useAuthStore()
    auth.user = {
      id: 'u1',
      username: 'student01',
      display_name: '张同学',
      status: 'active',
      roles: [{ id: 'r1', code: 'student', name: '普通学生' }],
      permissions: [
        'chat:use',
        'work_order:read',
        'community:read',
        'agent:run',
        'agent:run:read_own',
      ],
      created_at: NOW,
      version: 1,
    } as never
    auth.status = 'authenticated'

    const wrapper = mountChat()
    await wrapper.find('.chat__user').trigger('click')
    expect(wrapper.find('.chat__account-menu').text()).toContain('我的工单')
    expect(wrapper.find('.chat__account-menu').text()).toContain('我的活动')
    expect(wrapper.find('.chat__account-menu').text()).toContain('我的认领')
    expect(wrapper.find('.chat__account-menu').text()).toContain('待办审批')
    await clickButton(wrapper, '待办审批')
    expect(wrapper.find('.chat__account-notice').text()).toContain('请先打开一段对话')
    wrapper.unmount()
  })

  it('applies slash commands without sending them as messages and opens library selection', async () => {
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
    )
    const wrapper = mountChat()
    await vi.waitFor(() => expect(wrapper.text()).toContain('校区咨询'))

    const input = wrapper.find('textarea.chat__input')
    expect(wrapper.find('.chat__mode-launcher').exists()).toBe(false)
    expect(wrapper.find('.chat__header-actions').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('回答基于已发布知识库')
    expect(wrapper.find('.chat__selection-chips').text()).toContain('流式')
    expect(wrapper.find('.chat__selection-chips').text()).toContain('智能路由')

    await input.setValue('/')
    expect(wrapper.find('.chat__command-menu').exists()).toBe(true)
    expect(wrapper.findAll('.chat__command-option')[0]?.classes()).toContain(
      'chat__command-option--selected',
    )

    await input.trigger('keydown', { key: 'ArrowDown' })
    const syncOption = wrapper
      .findAll('.chat__command-option')
      .find((candidate) => candidate.text().includes('/sync'))
    expect(syncOption?.classes()).toContain('chat__command-option--selected')
    await input.trigger('keydown', { key: 'Enter' })
    expect((input.element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.find('.chat__selection-chips').text()).toContain('完整')

    await input.setValue('/stream')
    await input.trigger('keydown', { key: 'Enter' })
    expect((input.element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.find('.chat__selection-chips').text()).toContain('流式')

    await input.setValue('/lib')
    await input.trigger('keydown', { key: 'Enter' })
    expect((input.element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.find('.chat__kb-panel').exists()).toBe(true)
    expect(wrapper.find('.chat__kb-panel').text()).toContain('校规知识库')
    expect(wrapper.find('.chat__selection-chips').text()).toContain('知识库 1')
    expect(wrapper.find('.chat__selection-chips').text()).toContain('知识库问答')
    await input.setValue('/')
    expect(wrapper.text()).not.toContain('/agent')
    expect(wrapper.text()).not.toContain('/tool')
    wrapper.unmount()
  })

  it('selects real agents and tools from the backend catalog and executes an agent run inline', async () => {
    let createBody: Record<string, unknown> | null = null
    let listedConversationId = ''
    const run = {
      id: 'run-1',
      status: 'created',
      route: null,
      router_model: null,
      router_confidence: null,
      input_summary: '查询我的电费',
      final_answer: null,
      error_code: null,
      created_at: NOW,
      updated_at: NOW,
      finished_at: null,
    }
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/agents', () => HttpResponse.json(envelope({ items: [SERVICE_AGENT] }))),
      http.get('/api/v1/tools', () => HttpResponse.json(envelope({ items: [ELECTRICITY_TOOL] }))),
      http.post('/api/v1/conversations', () =>
        HttpResponse.json(envelope(CONVERSATION), { status: 201 }),
      ),
      http.get('/api/v1/conversations/:id/messages', () => HttpResponse.json(envelope(pageOf([])))),
      http.get('/api/v1/agent-runs', ({ request }) => {
        listedConversationId = new URL(request.url).searchParams.get('conversation_id') ?? ''
        return HttpResponse.json(envelope(pageOf([run])))
      }),
      http.post('/api/v1/agent-runs', async ({ request }) => {
        createBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(envelope(run), { status: 202 })
      }),
      http.get('/api/v1/agent-runs/run-1', () =>
        HttpResponse.json(
          envelope({
            run: {
              ...run,
              status: 'succeeded',
              route: 'service',
              final_answer: '当前余额 32.50 元。',
            },
            steps: [],
            tool_calls: [],
            approvals: [],
          }),
        ),
      ),
      http.get(
        '/api/v1/agent-runs/run-1/stream',
        () =>
          new Response(
            sse(
              'id: 1\nevent: route\ndata: {"sequence":1,"data":{"target_agent":"service"}}\n\n' +
                'id: 2\nevent: done\ndata: {"sequence":2,"data":{"status":"succeeded"}}\n\n',
            ),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
      ),
    )
    const auth = useAuthStore()
    auth.user = {
      id: 'u1',
      username: 'student01',
      display_name: '张同学',
      status: 'active',
      roles: [{ id: 'r1', code: 'model_engineer', name: '模型工程管理员' }],
      permissions: [
        'chat:use',
        'agent:run',
        'agent:run:read_own',
        'agent:catalog:read',
        'tool:catalog:read',
        'model:read',
      ],
      created_at: NOW,
      version: 1,
    } as never
    auth.status = 'authenticated'

    const wrapper = mountChat()
    const input = wrapper.find('textarea.chat__input')
    await input.setValue('/agent')
    await input.trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(wrapper.text()).toContain('校园服务 Agent'))
    await wrapper.find('.chat__selection-item input').setValue(true)
    expect(wrapper.find('.chat__selection-chips').text()).toContain('Agent 1')

    await input.setValue('/tool')
    await input.trigger('keydown', { key: 'Enter' })
    await wrapper.find('.chat__selection-item input').setValue(true)
    expect(wrapper.find('.chat__selection-chips').text()).toContain('Tool 1')

    await input.setValue('查询我的电费')
    await clickButton(wrapper, '发送')
    await vi.waitFor(() => expect(wrapper.text()).toContain('当前余额 32.50 元。'))
    expect(createBody).toMatchObject({
      conversation_id: CONVERSATION.id,
      mode: 'service',
      context: {
        requested_agent_codes: ['service_agent'],
        requested_tool_names: ['electricity.get_balance'],
      },
    })
    expect(wrapper.text()).not.toContain('最近 Agent 任务')
    await wrapper.find('.chat__conversation').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('当前余额 32.50 元。'))
    expect(listedConversationId).toBe(CONVERSATION.id)
    wrapper.unmount()
  })

  it('sends ordinary input through an auto Agent Run bound to the current conversation', async () => {
    let createBody: Record<string, unknown> = {}
    const run = { id: 'run-auto', status: 'created', route: null, input_summary: '查询电费', final_answer: null, error_code: null, created_at: NOW, updated_at: NOW, finished_at: null }
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/conversations/conv-1/messages', () => HttpResponse.json(envelope(pageOf([])))),
      http.get('/api/v1/agent-runs', () => HttpResponse.json(envelope(pageOf([])))),
      http.post('/api/v1/agent-runs', async ({ request }) => {
        createBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(envelope(run), { status: 202 })
      }),
      http.get('/api/v1/agent-runs/run-auto', () => HttpResponse.json(envelope({ run: { ...run, status: 'succeeded', final_answer: '余额为 32.50 元。' }, steps: [], tool_calls: [], approvals: [] }))),
      http.get('/api/v1/agent-runs/run-auto/stream', () => new Response(sse('id: 1\nevent: done\ndata: {"sequence":1,"data":{"status":"succeeded"}}\n\n'), { headers: { 'Content-Type': 'text/event-stream' } })),
    )
    const auth=useAuthStore()
    auth.user={id:'u1',username:'student01',display_name:'张同学',status:'active',roles:[{id:'r1',code:'student',name:'普通学生'}],permissions:['chat:use','agent:run','agent:run:read_own'],created_at:NOW,version:1} as never
    auth.status='authenticated'
    const wrapper=mountChat()
    await vi.waitFor(() => expect(wrapper.text()).toContain('校区咨询'))
    await ask(wrapper,'查询电费')
    await vi.waitFor(() => expect(createBody).toMatchObject({conversation_id:'conv-1',mode:'auto',context:{}}))
    expect(createBody).toMatchObject({conversation_id:'conv-1',mode:'auto',context:{}})
    wrapper.unmount()
  })

  it('recovers an awaiting approval run after SSE EOF and blocks a duplicate Enter submission', async () => {
    let createCount = 0
    const createdRun = { id: 'run-approval', status: 'created', route: null, input_summary: '帮我充电费', final_answer: null, error_code: null, created_at: NOW, updated_at: NOW, finished_at: null }
    const awaitingRun = { ...createdRun, status: 'awaiting_approval' }
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/conversations/conv-1/messages', () => HttpResponse.json(envelope(pageOf([])))),
      http.get('/api/v1/agent-runs', () =>
        HttpResponse.json(envelope(pageOf(createCount > 0 ? [awaitingRun] : []))),
      ),
      http.post('/api/v1/agent-runs', () => {
        createCount += 1
        return HttpResponse.json(envelope(createdRun), { status: 202 })
      }),
      http.get('/api/v1/agent-runs/run-approval', () =>
        HttpResponse.json(envelope({ run: awaitingRun, steps: [], tool_calls: [], approvals: [] })),
      ),
      http.get('/api/v1/agent-runs/run-approval/stream', () =>
        new Response(
          sse('event: approval_required\ndata: {"sequence":1,"tool_name":"electricity.create_topup_request"}\n\n'),
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
      ),
    )
    const auth = useAuthStore()
    auth.user = { id: 'u1', username: 'student01', display_name: '张同学', status: 'active', roles: [{ id: 'r1', code: 'student', name: '普通学生' }], permissions: ['chat:use', 'agent:run', 'agent:run:read_own'], created_at: NOW, version: 1 } as never
    auth.status = 'authenticated'

    const wrapper = mountChat()
    await vi.waitFor(() => expect(wrapper.find('.chat__conversation').exists()).toBe(true))
    await wrapper.find('.chat__conversation').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('textarea.chat__input').exists()).toBe(true))
    const firstInput = wrapper.find('textarea.chat__input')
    await firstInput.setValue('帮我充电费')
    await firstInput.trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(createCount).toBe(1))
    await vi.waitFor(() => expect(wrapper.find('textarea.chat__input').attributes('disabled')).toBeDefined())
    expect(wrapper.text()).not.toContain('运行事件连接中断')

    const input = wrapper.find('textarea.chat__input')
    await input.setValue('帮我充电费')
    await input.trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(createCount).toBe(1))
    wrapper.unmount()
  })

  it('continues an input-required run from its last SSE event without replaying the old prompt', async () => {
    let postCount = 0
    let continuationCursor: string | null = null
    const baseRun = { id: 'run-continuation', status: 'created', route: 'service', input_summary: '帮我充电费', final_answer: null, error_code: null, created_at: NOW, updated_at: NOW, finished_at: null }
    const firstStep = { id: 'step-1', sequence: 1, agent_code: 'service_agent', step_type: 'generate', status: 'partial', input_summary: {}, output_summary: { answer: '请提供充值金额。', missing_slots: ['amount_cny'] }, error_code: null, started_at: NOW, finished_at: NOW }
    const continuationStep = { id: 'step-2', sequence: 2, agent_code: 'service_agent', step_type: 'generate', status: 'awaiting_approval', input_summary: { continuation_input: '充50元' }, output_summary: {}, error_code: null, started_at: NOW, finished_at: null }
    const currentRun = () => ({ ...baseRun, status: postCount >= 2 ? 'awaiting_approval' : 'awaiting_input' })
    const currentDetail = () => ({ run: currentRun(), steps: postCount >= 2 ? [firstStep, continuationStep] : [firstStep], tool_calls: [], approvals: [] })
    server.use(
      http.get('/api/v1/conversations', () => HttpResponse.json(envelope(pageOf([CONVERSATION])))),
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.get('/api/v1/conversations/conv-1/messages', () => HttpResponse.json(envelope(pageOf([])))),
      http.get('/api/v1/agent-runs', () => HttpResponse.json(envelope(pageOf(postCount ? [currentRun()] : [])))),
      http.post('/api/v1/agent-runs', () => {
        postCount += 1
        return HttpResponse.json(envelope(postCount === 1 ? baseRun : { ...baseRun, status: 'awaiting_input' }), { status: 202 })
      }),
      http.get('/api/v1/agent-runs/run-continuation', () => HttpResponse.json(envelope(currentDetail()))),
      http.get('/api/v1/agent-runs/run-continuation/stream', ({ request }) => {
        const cursor = request.headers.get('Last-Event-ID')
        if (cursor) {
          continuationCursor = cursor
          return new Response(
            sse('id: 3\nevent: approval_required\ndata: {"sequence":3,"tool_name":"electricity.create_topup_request"}\n\n'),
            { headers: { 'Content-Type': 'text/event-stream' } },
          )
        }
        return new Response(
          sse('id: 1\nevent: route\ndata: {"sequence":1,"target_agent":"service"}\n\nid: 2\nevent: input_required\ndata: {"sequence":2,"status":"awaiting_input"}\n\n'),
          { headers: { 'Content-Type': 'text/event-stream' } },
        )
      }),
    )
    const auth = useAuthStore()
    auth.user = { id: 'u1', username: 'student01', display_name: '张同学', status: 'active', roles: [{ id: 'r1', code: 'student', name: '普通学生' }], permissions: ['chat:use', 'agent:run', 'agent:run:read_own'], created_at: NOW, version: 1 } as never
    auth.status = 'authenticated'

    const wrapper = mountChat()
    await vi.waitFor(() => expect(wrapper.find('.chat__conversation').exists()).toBe(true))
    await wrapper.find('.chat__conversation').trigger('click')
    let input = wrapper.find('textarea.chat__input')
    await input.setValue('帮我充电费')
    await input.trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(wrapper.text()).toContain('请提供充值金额。'))
    await vi.waitFor(() => expect(wrapper.find('textarea.chat__input').attributes('disabled')).toBeUndefined())

    input = wrapper.find('textarea.chat__input')
    await input.setValue('充50元')
    await clickButton(wrapper, '发送')
    await vi.waitFor(() => expect(continuationCursor).toBe('2'))
    await vi.waitFor(() => expect(wrapper.find('textarea.chat__input').attributes('disabled')).toBeDefined())
    expect(postCount).toBe(2)
    expect(wrapper.text()).toContain('请提供充值金额。')
    expect(wrapper.text()).toContain('充50元')
    wrapper.unmount()
  })

  it('uses /learn without sources and sends the real learn contract mode', async () => {
    let requestBody: Record<string, unknown> = {}
    const message=makeMessage({id:'learn-1',content:'通用模型回答，无校内资料引用\n\n导数描述函数的瞬时变化率。',citations:[]})
    useChatHandlers(message,'event: meta\ndata: {"conversation_id":"conv-1","message_id":"learn-1","request_id":"r"}\n\nevent: delta\ndata: {"sequence":1,"content":"通用模型回答，无校内资料引用\\n\\n导数描述函数的瞬时变化率。"}\n\nevent: sources\ndata: {"citations":[]}\n\nevent: done\ndata: {"finish_reason":"stop","usage":{}}\n\n')
    server.use(http.post('/api/v1/chat/stream',async ({request})=>{requestBody=(await request.json()) as Record<string,unknown>;return new Response(sse('event: meta\ndata: {"conversation_id":"conv-1","message_id":"learn-1","request_id":"r"}\n\nevent: done\ndata: {"finish_reason":"stop","usage":{}}\n\n'),{headers:{'Content-Type':'text/event-stream'}})}))
    const wrapper=mountChat()
    await vi.waitFor(()=>expect(wrapper.text()).toContain('校区咨询'))
    await wrapper.find('.chat__conversation').trigger('click')
    const input=wrapper.find('textarea.chat__input')
    await input.setValue('/learn');await input.trigger('keydown',{key:'Enter'})
    await wrapper.find('.chat__kb-item input').setValue(false)
    await input.setValue('讲解导数');await clickButton(wrapper,'发送')
    await vi.waitFor(()=>expect(requestBody).toMatchObject({mode:'learn',knowledge_base_ids:[]}))
    expect(wrapper.find('.chat__selection-chips').text()).toContain('学习辅导')
    wrapper.unmount()
  })

  it('流式回答按 meta→delta→sources→done 渲染，经 getMessage 恢复并展示引用；反馈幂等提交', async () => {
    const message = makeMessage({
      id: 'm1',
      content: '四川大学望江校区位于成都市武侯区。',
      citations: [CITATION],
      model: 'deepseek-v4-pro',
      finish_reason: 'stop',
    })
    useChatHandlers(
      message,
      'event: meta\ndata: {"conversation_id":"conv-1","message_id":"m1","request_id":"r1"}\n\n' +
        'event: delta\ndata: {"sequence":1,"content":"四川大学"}\n\n' +
        'event: delta\ndata: {"sequence":2,"content":"望江校区"}\n\n' +
        `event: sources\ndata: {"citations":[${JSON.stringify(CITATION)}]}\n\n` +
        'event: done\ndata: {"finish_reason":"stop","usage":{"prompt_tokens":10,"completion_tokens":8}}\n\n',
    )
    const feedbackKeys: (string | null)[] = []
    server.use(
      http.post('/api/v1/messages/m1/feedback', async ({ request }) => {
        feedbackKeys.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json(
          envelope({ id: 'fb-1', message_id: 'm1', rating: 1, correction: null, created_at: NOW }),
          { status: 201 },
        )
      }),
    )
    const wrapper = mountChat()
    await vi.waitFor(() => expect(wrapper.text()).toContain('校区咨询'))
    await askWithLibrary(wrapper, '望江校区地址')
    // 最终内容以后端 getMessage 恢复为准
    await vi.waitFor(() => expect(wrapper.text()).toContain('四川大学望江校区位于成都市武侯区。'))
    // 引用侧栏：文档名、位置、页码、摘录
    await clickButton(wrapper, '引用 1')
    expect(wrapper.text()).toContain('校区服务指南')
    expect(wrapper.text()).toContain('第一章 校区分布')
    expect(wrapper.text()).toContain('第 3 页')
    expect(wrapper.text()).toContain('望江校区位于成都市武侯区一环路南一段24号。')
    // 反馈：成功后按钮进入已反馈状态，且携带幂等键
    const like = wrapper.findAll('button').find((candidate) => candidate.text() === '👍')
    expect(like).toBeTruthy()
    await like?.trigger('click')
    await vi.waitFor(() => expect(feedbackKeys).toHaveLength(1))
    expect(feedbackKeys[0]).toBeTruthy()
    await vi.waitFor(() => expect(like?.attributes('disabled')).toBeDefined())
    wrapper.unmount()
  })

  it('fallback 回答保留正文但隐藏内部状态标识和伪引用', async () => {
    const message = makeMessage({
      id: 'm2',
      status: 'fallback',
      content: '未在已发布知识库中找到相关内容，请换个问法或联系服务台。',
      fallback_reason: 'no_qualified_retrieval',
      finish_reason: 'fallback',
      citations: [],
    })
    useChatHandlers(
      message,
      'event: meta\ndata: {"conversation_id":"conv-1","message_id":"m2","request_id":"r2"}\n\n' +
        'event: sources\ndata: {"citations":[]}\n\n' +
        'event: done\ndata: {"finish_reason":"fallback","usage":{"prompt_tokens":0,"completion_tokens":0}}\n\n',
    )
    const wrapper = mountChat()
    await vi.waitFor(() => expect(wrapper.text()).toContain('校区咨询'))
    await askWithLibrary(wrapper, '量子引力')
    await vi.waitFor(() => expect(wrapper.text()).toContain('未在已发布知识库中找到相关内容'))
    expect(wrapper.text()).not.toContain('兜底回答')
    const citationButtons = wrapper
      .findAll('button')
      .filter((candidate) => candidate.text().includes('引用'))
    expect(citationButtons).toHaveLength(0)
    wrapper.unmount()
  })
})
