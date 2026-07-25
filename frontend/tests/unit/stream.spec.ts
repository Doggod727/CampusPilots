import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { isApiError } from '@/api/client/errors'
import { tokenStore } from '@/api/client/tokenStore'
import { streamAgentRun, type AgentRunEvent } from '@/api/stream/agentStream'
import { streamChatCompletion, type ChatStreamHandlers } from '@/api/stream/chatStream'
import { installNoPersistenceGuard } from '@/app/bootstrap/noPersistence'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function sse(text: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
}

function events(handlers: ChatStreamHandlers) {
  return handlers
}

describe('chat SSE', () => {
  it('streams meta/delta/sources/done in order and dedupes delta sequences', async () => {
    server.use(
      http.post(
        '/api/v1/chat/stream',
        () =>
          new Response(
            sse(
              'event: meta\ndata: {"conversation_id":"c1","message_id":"m1","request_id":"r1"}\n\n' +
                'event: delta\ndata: {"sequence":1,"content":"四川"}\n\n' +
                'event: delta\ndata: {"sequence":2,"content":"大学"}\n\n' +
                'event: delta\ndata: {"sequence":2,"content":"大学"}\n\n' +
                'event: delta\ndata: {"sequence":3,"content":"校区"}\n\n' +
                'event: sources\ndata: {"citations":[{"citation_no":1}]}\n\n' +
                'event: done\ndata: {"finish_reason":"stop","usage":{"prompt_tokens":1}}\n\n',
            ),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
      ),
    )
    const seen: string[] = []
    await streamChatCompletion(
      { question: '校区地址', knowledge_base_ids: ['kb'] },
      events({
        onMeta: () => seen.push('meta'),
        onDelta: (delta) => seen.push(`d${delta.sequence}`),
        onSources: () => seen.push('sources'),
        onDone: () => seen.push('done'),
      }),
    )
    expect(seen).toEqual(['meta', 'd1', 'd2', 'd3', 'sources', 'done'])
  })

  it('handles the fallback stream shape (meta→sources→done)', async () => {
    server.use(
      http.post(
        '/api/v1/chat/stream',
        () =>
          new Response(
            sse(
              'event: meta\ndata: {"conversation_id":"c2","message_id":"m2","request_id":"r2"}\n\n' +
                'event: sources\ndata: {"citations":[]}\n\n' +
                'event: done\ndata: {"finish_reason":"fallback","usage":{"prompt_tokens":0}}\n\n',
            ),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
      ),
    )
    const seen: string[] = []
    let reason = ''
    await streamChatCompletion(
      { question: '量子引力', knowledge_base_ids: ['kb'] },
      {
        onMeta: () => seen.push('meta'),
        onDelta: () => seen.push('delta'),
        onSources: () => seen.push('sources'),
        onDone: (payload) => {
          seen.push('done')
          reason = payload.finish_reason
        },
      },
    )
    expect(seen).toEqual(['meta', 'sources', 'done'])
    expect(reason).toBe('fallback')
  })

  it('surfaces provider error events', async () => {
    server.use(
      http.post(
        '/api/v1/chat/stream',
        () =>
          new Response(
            sse(
              'event: meta\ndata: {"conversation_id":"c3","message_id":"m3","request_id":"r3"}\n\n' +
                'event: error\ndata: {"code":"AGENT_PROVIDER_UNAVAILABLE","message":"回答生成失败","retryable":false,"message_id":"m3"}\n\n',
            ),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
      ),
    )
    const onError = vi.fn()
    await streamChatCompletion({ question: '校区', knowledge_base_ids: ['kb'] }, { onError })
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'AGENT_PROVIDER_UNAVAILABLE', retryable: false }),
    )
  })
})

describe('agent run SSE', () => {
  it('treats a silent EOF as a disconnect that requires state recovery', async () => {
    server.use(
      http.get(
        '/api/v1/agent-runs/run-eof/stream',
        () =>
          new Response(
            sse('event: approval_required\ndata: {"sequence":1,"tool_name":"electricity.create_topup_request"}\n\n'),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
      ),
    )

    const error = await streamAgentRun('run-eof', {}).catch((value: unknown) => value)
    expect(error).toBeInstanceOf(Error)
    expect((error as Error).message).toContain('before a terminal event')
  })

  it('releases the stream when the agent asks for continuation input', async () => {
    server.use(
      http.get(
        '/api/v1/agent-runs/run-input/stream',
        () =>
          new Response(
            sse(
              'event: route\ndata: {"sequence":1,"target_agent":"community"}\n\n' +
                'event: input_required\ndata: {"sequence":2,"status":"awaiting_input","message":"请补充地点和分类"}\n\n',
            ),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
      ),
    )
    const seen: AgentRunEvent[] = []
    const done = vi.fn()
    await streamAgentRun('run-input', { onEvent: (event) => seen.push(event), onDone: done })
    expect(seen.map((event) => event.event)).toEqual(['route'])
    expect(done).toHaveBeenCalledWith(
      expect.objectContaining({ event: 'input_required', sequence: 2 }),
    )
  })

  it('sends Last-Event-ID and skips replayed sequences', async () => {
    let lastEventId: string | null = null
    server.use(
      http.get('/api/v1/agent-runs/run-1/stream', ({ request }) => {
        lastEventId = request.headers.get('Last-Event-ID')
        return new Response(
          sse(
            'event: route\ndata: {"sequence":4,"target_agent":"service"}\n\n' +
              'event: agent_step\ndata: {"sequence":5,"agent_code":"service_agent"}\n\n' +
              'event: agent_step\ndata: {"sequence":5,"agent_code":"service_agent"}\n\n' +
              'event: done\ndata: {"sequence":6,"status":"succeeded"}\n\n',
          ),
          { headers: { 'Content-Type': 'text/event-stream' } },
        )
      }),
    )
    tokenStore.set('probe')
    const seen: AgentRunEvent[] = []
    const done = vi.fn()
    await streamAgentRun('run-1', { onEvent: (event) => seen.push(event), onDone: done }, { lastEventId: 3 })
    expect(lastEventId).toBe('3')
    expect(seen.map((event) => event.sequence)).toEqual([4, 5])
    expect(done).toHaveBeenCalledWith(expect.objectContaining({ sequence: 6 }))
    tokenStore.clear()
  })

  it('maps HTTP failures to ApiError', async () => {
    server.use(
      http.get(
        '/api/v1/agent-runs/run-x/stream',
        () =>
          new Response(JSON.stringify({ code: 'AGENT_EVENT_CURSOR_INVALID', message: '游标无效', details: [], request_id: 'r9' }), {
            status: 409,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    )
    const error = await streamAgentRun('run-x', {}).catch((value: unknown) => value)
    expect(isApiError(error)).toBe(true)
    if (isApiError(error)) {
      expect(error.status).toBe(409)
      expect(error.code).toBe('AGENT_EVENT_CURSOR_INVALID')
    }
  })
})

describe('no-persistence guard', () => {
  it('blocks storage writes and indexedDB after install', () => {
    installNoPersistenceGuard()
    expect(() => window.localStorage.setItem('a', 'b')).toThrow(/禁用/)
    expect(() => window.sessionStorage.clear()).toThrow(/禁用/)
    if ('indexedDB' in window) {
      expect(() => window.indexedDB).toThrow(/禁用/)
    }
  })
})
