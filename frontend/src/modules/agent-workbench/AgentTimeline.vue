<script setup lang="ts">
import type { AgentRunEvent } from '@/api/stream/agentStream'

defineProps<{
  events: readonly AgentRunEvent[]
  live: boolean
}>()

const STAGE_META: Record<string, { label: string; tone: 'thinking' | 'read' | 'edit' | 'grep' | 'done' }> = {
  route: { label: '路由决策', tone: 'thinking' },
  agent_step: { label: 'Agent 执行', tone: 'read' },
  tool_call: { label: 'Tool 调用', tone: 'edit' },
  handoff: { label: 'Agent 交接', tone: 'grep' },
  approval_required: { label: '需要审批', tone: 'done' },
  delta: { label: '生成中', tone: 'read' },
  sources: { label: '引用来源', tone: 'grep' },
  done: { label: '完成', tone: 'done' },
  error: { label: '失败', tone: 'done' },
}

function describe(event: AgentRunEvent): string {
  const data = event.data
  switch (event.event) {
    case 'route':
      return `路由到 ${String(data.target_agent ?? data.route ?? '未知')}（置信度 ${String(data.confidence ?? '-')}）`
    case 'agent_step':
      return `${String(data.agent_code ?? 'agent')} · ${String(data.status ?? '')}`
    case 'tool_call':
      return String(data.tool_name ?? 'tool')
    case 'approval_required':
      return `等待审批：${String(data.tool_name ?? '')}`
    case 'handoff':
      return `${String(data.from_agent ?? '')} → ${String(data.to_agent ?? '')}`
    case 'done':
      return `终态：${String(data.status ?? '')}`
    case 'error':
      return `错误：${String(data.code ?? data.message ?? '')}`
    default:
      return ''
  }
}
</script>

<template>
  <div class="timeline" aria-live="polite">
    <p v-if="live" class="timeline__live"><span class="timeline__pulse" aria-hidden="true" />实时更新中</p>
    <ol v-if="events.length" class="timeline__list">
      <li v-for="event in events" :key="event.sequence" class="timeline__item" :class="`timeline__item--${STAGE_META[event.event]?.tone ?? 'read'}`">
        <span class="timeline__dot" aria-hidden="true" />
        <div class="timeline__body">
          <p class="timeline__stage">{{ STAGE_META[event.event]?.label ?? event.event }}</p>
          <p class="timeline__desc">{{ describe(event) }}</p>
        </div>
        <span class="timeline__seq">#{{ event.sequence }}</span>
      </li>
    </ol>
    <p v-else class="timeline__empty">等待运行事件…</p>
  </div>
</template>

<style scoped>
.timeline {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.timeline__live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  color: var(--cp-info);
}

.timeline__pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--cp-info);
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.3;
  }
}

.timeline__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
}

.timeline__item {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: var(--cp-space-3);
  padding: var(--cp-space-2) 0 var(--cp-space-3) var(--cp-space-1);
  border-left: 2px solid var(--cp-hairline);
  margin-left: 6px;
  padding-left: var(--cp-space-5);
}

.timeline__item:last-child {
  border-left-color: transparent;
}

.timeline__dot {
  position: absolute;
  left: -7px;
  top: 10px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid var(--cp-surface-card);
}

.timeline__item--thinking .timeline__dot {
  background: var(--cp-timeline-thinking);
}

.timeline__item--read .timeline__dot {
  background: var(--cp-timeline-read);
}

.timeline__item--edit .timeline__dot {
  background: var(--cp-timeline-edit);
}

.timeline__item--grep .timeline__dot {
  background: var(--cp-timeline-grep);
}

.timeline__item--done .timeline__dot {
  background: var(--cp-timeline-done);
}

.timeline__body {
  flex: 1;
}

.timeline__stage {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink);
}

.timeline__desc {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.timeline__seq {
  font-size: 11px;
  color: var(--cp-muted-soft);
  font-family: var(--cp-font-mono);
}

.timeline__empty {
  margin: 0;
  color: var(--cp-muted-soft);
  font-size: 13px;
}
</style>
