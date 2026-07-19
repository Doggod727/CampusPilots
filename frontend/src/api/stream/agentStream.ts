import { configureHttpClient } from '@/api/client/http'
import { toApiError } from '@/api/client/errors'
import { tokenStore } from '@/api/client/tokenStore'

import { parseSseStream } from './sse'

export const AGENT_RUN_EVENTS = [
  'meta',
  'route',
  'agent_step',
  'tool_call',
  'approval_required',
  'handoff',
  'delta',
  'sources',
  'done',
  'error',
] as const

export type AgentRunEventName = (typeof AGENT_RUN_EVENTS)[number]

export interface AgentRunEvent {
  sequence: number
  event: AgentRunEventName
  data: Record<string, unknown>
}

export interface AgentStreamHandlers {
  onEvent?: (event: AgentRunEvent) => void
  onDone?: (event: AgentRunEvent) => void
  onError?: (event: AgentRunEvent) => void
}

export interface AgentStreamOptions {
  /** 断线重连游标：作为 Last-Event-ID 发送，仅回放增量事件。 */
  lastEventId?: number
  signal?: AbortSignal
}

/** GET /api/v1/agent-runs/{run_id}/stream：按 sequence 去重排序，支持 Last-Event-ID 重放。 */
export async function streamAgentRun(
  runId: string,
  handlers: AgentStreamHandlers,
  options: AgentStreamOptions = {},
): Promise<void> {
  configureHttpClient()
  const headers = new Headers({ 'X-Request-Id': crypto.randomUUID() })
  const token = tokenStore.get()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (options.lastEventId !== undefined) {
    headers.set('Last-Event-ID', String(options.lastEventId))
  }
  const path = `/api/v1/agent-runs/${runId}/stream`
  let response: Response
  try {
    response = await fetch(path, { headers, signal: options.signal })
  } catch (error) {
    if (!(error instanceof TypeError) || !options.signal || !error.message.includes('signal')) throw error
    response = await fetch(path, { headers })
  }
  if (!response.ok || !response.body) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      body = null
    }
    throw toApiError(response.status, body, response.headers.get('X-Request-Id'))
  }
  let lastSequence = options.lastEventId ?? 0
  for await (const frame of parseSseStream(response.body)) {
    if (frame.event === 'heartbeat' || frame.event === 'keep-alive') {
      continue
    }
    if (!frame.data) {
      continue
    }
    const envelope = JSON.parse(frame.data) as Record<string, unknown>
    const isWrapped = 'sequence' in envelope && 'data' in envelope && typeof envelope.data === 'object'
    const payload = (isWrapped ? (envelope.data as Record<string, unknown>) : envelope)
    const sequence = Number(envelope.sequence ?? frame.id ?? 0)
    if (sequence <= lastSequence) {
      continue
    }
    lastSequence = sequence
    const event: AgentRunEvent = {
      sequence,
      event: frame.event as AgentRunEventName,
      data: payload,
    }
    if (frame.event === 'done') {
      handlers.onDone?.(event)
      return
    }
    if (frame.event === 'error') {
      handlers.onError?.(event)
      return
    }
    handlers.onEvent?.(event)
  }
}

/** 当前已接收的最大 sequence，用于断线后重连。 */
export function lastSequenceOf(events: readonly AgentRunEvent[]): number {
  return events.reduce((max, event) => Math.max(max, event.sequence), 0)
}
