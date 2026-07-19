import { mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { tokenStore } from '@/api/client'
import ChatView from '@/modules/chat-kb/ChatView.vue'
import IngestionView from '@/modules/chat-kb/IngestionView.vue'
import KnowledgeBasesView from '@/modules/chat-kb/KnowledgeBasesView.vue'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const NOW = '2026-07-18T00:00:00Z'

const envelope = (data: unknown) => ({ code: 'OK', message: 'success', data, request_id: 'r', timestamp: NOW })
const errorEnvelope = (code: string, message: string) => ({ code, message, details: [], request_id: 'r', timestamp: NOW })
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

beforeEach(() => {
  setActivePinia(createPinia())
  tokenStore.set('test-token')
})

describe('KnowledgeBasesView', () => {
  it('删除被占用知识库时映射 409 KNOWLEDGE_BASE_IN_USE', async () => {
    server.use(
      http.get('/api/v1/knowledge-bases', () => HttpResponse.json(envelope(pageOf([KB])))),
      http.delete('/api/v1/knowledge-bases/kb-1', () =>
        HttpResponse.json(errorEnvelope('KNOWLEDGE_BASE_IN_USE', '知识库仍被文档或会话引用'), { status: 409 }),
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
      http.get('/api/v1/knowledge-bases/kb-1/documents', () => HttpResponse.json(envelope(pageOf([])))),
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
      http.get('/api/v1/conversations/conv-1/messages', () => HttpResponse.json(envelope(pageOf([])))),
      http.post('/api/v1/chat/stream', () => new Response(sse(streamBody), { headers: { 'Content-Type': 'text/event-stream' } })),
      http.get(`/api/v1/messages/${message.id as string}`, () => HttpResponse.json(envelope(message))),
    )
  }

  async function ask(wrapper: ReturnType<typeof mount>, question: string) {
    await wrapper.find('.chat__conversation').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('暂无消息'))
    await wrapper.find('textarea.chat__input').setValue(question)
    await clickButton(wrapper, '发送')
  }

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
    const wrapper = mount(ChatView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('校区咨询'))
    await ask(wrapper, '望江校区地址')
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

  it('fallback 回答展示兜底标识且不显示伪引用', async () => {
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
    const wrapper = mount(ChatView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('校区咨询'))
    await ask(wrapper, '量子引力')
    await vi.waitFor(() => expect(wrapper.text()).toContain('未在已发布知识库中找到相关内容'))
    expect(wrapper.text()).toContain('兜底回答')
    const citationButtons = wrapper.findAll('button').filter((candidate) => candidate.text().includes('引用'))
    expect(citationButtons).toHaveLength(0)
    wrapper.unmount()
  })
})
