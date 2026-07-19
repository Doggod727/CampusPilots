import { configureHttpClient } from '@/api/client/http'
import { toApiError } from '@/api/client/errors'
import { tokenStore } from '@/api/client/tokenStore'

import { parseSseStream } from './sse'

export interface ChatCitation {
  citation_no: number
  document_id: string
  document_title: string
  source_location: string
  page_number: number | null
  quote_excerpt: string
  relevance_score: number
}

export interface ChatStreamHandlers {
  onMeta?: (payload: { conversation_id: string; message_id: string; request_id: string }) => void
  onDelta?: (payload: { sequence: number; content: string }) => void
  onSources?: (payload: { citations: ChatCitation[] }) => void
  onDone?: (payload: { finish_reason: string; usage: Record<string, number> }) => void
  onError?: (payload: { code: string; message: string; retryable: boolean; message_id: string | null }) => void
}

export interface ChatStreamRequest {
  question: string
  knowledge_base_ids: string[]
  conversation_id?: string | null
}

async function streamRequest(path: string, init: RequestInit): Promise<Response> {
  configureHttpClient()
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  headers.set('X-Request-Id', crypto.randomUUID())
  headers.set('Idempotency-Key', crypto.randomUUID())
  const token = tokenStore.get()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok || !response.body) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      body = null
    }
    throw toApiError(response.status, body, response.headers.get('X-Request-Id'))
  }
  return response
}

/** POST /api/v1/chat/stream：meta → delta* → sources → done/error 固定顺序（含兜底流）。 */
export async function streamChatCompletion(
  request: ChatStreamRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await streamRequest('/api/v1/chat/stream', {
    method: 'POST',
    body: JSON.stringify({
      question: request.question,
      knowledge_base_ids: request.knowledge_base_ids,
      conversation_id: request.conversation_id ?? null,
    }),
    signal,
  })
  let lastSequence = 0
  for await (const frame of parseSseStream(response.body!)) {
    if (!frame.data) {
      continue
    }
    const payload = JSON.parse(frame.data) as Record<string, unknown>
    switch (frame.event) {
      case 'meta':
        handlers.onMeta?.(payload as { conversation_id: string; message_id: string; request_id: string })
        break
      case 'delta': {
        const sequence = Number(payload.sequence ?? 0)
        if (sequence > lastSequence) {
          lastSequence = sequence
          handlers.onDelta?.(payload as { sequence: number; content: string })
        }
        break
      }
      case 'sources':
        handlers.onSources?.(payload as { citations: ChatCitation[] })
        break
      case 'done':
        handlers.onDone?.(payload as { finish_reason: string; usage: Record<string, number> })
        return
      case 'error':
        handlers.onError?.(payload as { code: string; message: string; retryable: boolean; message_id: string | null })
        return
      default:
        break
    }
  }
}
