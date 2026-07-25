import { onUnmounted, ref, type Ref } from 'vue'

import { streamAgentRun, type AgentRunEvent } from '@/api/stream/agentStream'

/** 管理单个 Agent Run 的 SSE 生命周期：回放、去重、断线重连一次、卸载中止。 */
export function useAgentRunStream(runId: Ref<string>) {
  const events = ref<AgentRunEvent[]>([])
  const live = ref(false)
  const failed = ref(false)
  let controller: AbortController | null = null
  let lastSequence = 0
  let reconnectAttempted = false

  function push(event: AgentRunEvent, onTerminal: (() => void) | undefined, terminal: boolean) {
    lastSequence = Math.max(lastSequence, event.sequence)
    events.value = [...events.value, event]
    if (terminal) {
      live.value = false
      onTerminal?.()
    }
  }

  async function consume(fromSequence: number, onTerminal?: () => void): Promise<void> {
    await streamAgentRun(
      runId.value,
      {
        onEvent: (event) => push(event, undefined, false),
        onDone: (event) => push(event, onTerminal, true),
        onError: (event) => push(event, onTerminal, true),
      },
      { signal: controller?.signal, ...(fromSequence > 0 ? { lastEventId: fromSequence } : {}) },
    )
  }

  async function start(onTerminal?: () => void): Promise<void> {
    stop()
    controller = new AbortController()
    live.value = true
    failed.value = false
    try {
      await consume(lastSequence, onTerminal)
      live.value = false
    } catch {
      if (controller?.signal.aborted) {
        return
      }
      live.value = false
      if (!reconnectAttempted && lastSequence > 0) {
        // 断线：以最大 sequence 重连一次（Last-Event-ID 增量重放）
        reconnectAttempted = true
        live.value = true
        try {
          await consume(lastSequence, onTerminal)
          live.value = false
          return
        } catch {
          live.value = false
        }
      }
      failed.value = true
    }
  }

  function stop(): void {
    controller?.abort()
    controller = null
    live.value = false
  }

  onUnmounted(stop)

  return { events, live, failed, start, stop }
}
