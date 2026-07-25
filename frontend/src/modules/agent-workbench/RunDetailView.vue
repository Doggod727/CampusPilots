<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import { cancelAgentRun, getAgentRun } from '@/api/generated'
import type { AgentRunDetailData } from '@/api/generated'
import AgentTimeline from '@/modules/agent-workbench/AgentTimeline.vue'
import ApprovalCards from '@/modules/agent-workbench/ApprovalCards.vue'
import { useAgentRunStream } from '@/modules/agent-workbench/composables/useAgentRunStream'
import { EmptyState, ErrorState, PageHeader, StatusBadge, UiButton, UiCard, UiSkeleton } from '@/shared/ui'

const ACTIVE_STATUSES = new Set(['created', 'routing', 'running', 'awaiting_approval'])

const route = useRoute()
const runId = computed(() => route.params.runId as string)

const detail = ref<AgentRunDetailData | null>(null)
const loading = ref(true)
const notFound = ref(false)
const failed = ref(false)
const cancelling = ref(false)
const cancelNotice = ref('')
const { events: streamEvents, live: streamLive, start: startStream } = useAgentRunStream(runId)

const canCancel = computed(() => detail.value !== null && ACTIVE_STATUSES.has(detail.value.run.status) && !cancelling.value)

async function load() {
  if (!detail.value) {
    loading.value = true
  }
  failed.value = false
  notFound.value = false
  try {
    const response = await callApi(() => getAgentRun({ path: { run_id: runId.value } }))
    detail.value = response.data
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound.value = true
    } else {
      failed.value = true
    }
  } finally {
    loading.value = false
  }
}

async function cancel() {
  if (!canCancel.value) {
    return
  }
  cancelling.value = true
  cancelNotice.value = ''
  try {
    await callApi(() =>
      cancelAgentRun({
        path: { run_id: runId.value },
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      }),
    )
    cancelNotice.value = '已请求取消。'
    await load()
  } catch (error) {
    cancelNotice.value = error instanceof ApiError && error.status === 409 ? '运行已进入终态，无需取消。' : '取消失败，请稍后重试。'
  } finally {
    cancelling.value = false
  }
}

const duration = computed(() => {
  const run = detail.value?.run
  if (!run?.finished_at) return null
  const ms = new Date(run.finished_at).getTime() - new Date(run.created_at).getTime()
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  await load()
  if (detail.value) {
    // 先读详情再从服务端重放事件（刷新/重进恢复），终态后自动刷新详情
    void startStream(() => {
      void load()
    })
  }
})

// 新审批产生时刷新详情：让审批卡（批准/拒绝）即时出现
watch(streamEvents, (events) => {
  const latest = events[events.length - 1]
  if (latest?.event === 'approval_required') {
    void load()
  }
})
</script>

<template>
  <div class="detail">
    <UiSkeleton v-if="loading" :lines="5" />
    <EmptyState v-else-if="notFound" title="运行不存在" description="该运行不存在或无权访问" />
    <ErrorState v-else-if="failed" title="详情加载失败" @retry="load" />
    <template v-else-if="detail">
      <PageHeader title="Run 详情">
        <div class="detail__actions">
          <StatusBadge :status="detail.run.status" />
          <UiButton v-if="canCancel" variant="danger" size="sm" :loading="cancelling" @click="cancel">取消运行</UiButton>
        </div>
      </PageHeader>
      <p v-if="cancelNotice" class="detail__cancel-note" role="status">{{ cancelNotice }}</p>

      <UiCard class="detail__summary" padding="md">
        <dl class="detail__grid">
          <div><dt>输入</dt><dd>{{ detail.run.input_summary }}</dd></div>
          <div><dt>路由</dt><dd>{{ detail.run.route ?? 'auto' }}</dd></div>
          <div><dt>创建时间</dt><dd>{{ formatTime(detail.run.created_at) }}</dd></div>
          <div v-if="duration"><dt>耗时</dt><dd>{{ duration }}</dd></div>
          <div v-if="detail.run.error_code"><dt>错误码</dt><dd class="detail__error">{{ detail.run.error_code }}</dd></div>
        </dl>
        <div v-if="detail.run.final_answer" class="detail__answer">
          <h3>最终回答</h3>
          <p>{{ detail.run.final_answer }}</p>
        </div>
      </UiCard>

      <section class="detail__section">
        <h2 class="detail__heading">运行时间线</h2>
        <UiCard padding="md">
          <AgentTimeline :events="streamEvents" :live="streamLive" />
        </UiCard>
      </section>

      <section v-if="detail.steps.length" class="detail__section">
        <h2 class="detail__heading">步骤（{{ detail.steps.length }}）</h2>
        <UiCard v-for="step in detail.steps" :key="step.id" class="detail__step" padding="md">
          <div class="detail__step-head">
            <span class="detail__step-seq">#{{ step.sequence }}</span>
            <code>{{ step.agent_code }}</code>
            <StatusBadge :status="step.status" />
          </div>
        </UiCard>
      </section>

      <section v-if="detail.tool_calls.length" class="detail__section">
        <h2 class="detail__heading">Tool 调用（{{ detail.tool_calls.length }}）</h2>
        <UiCard v-for="call in detail.tool_calls" :key="call.id" class="detail__step" padding="md">
          <div class="detail__step-head">
            <code>{{ call.tool_name }}</code>
            <StatusBadge :status="call.status" />
            <span class="detail__risk">风险 {{ call.risk_level }}</span>
            <span v-if="call.duration_ms != null" class="detail__dur">{{ call.duration_ms }}ms</span>
          </div>
        </UiCard>
      </section>

      <ApprovalCards
        v-if="detail.approvals.length"
        :run-id="runId"
        :approvals="detail.approvals"
        @decided="load"
      />
    </template>
  </div>
</template>

<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.detail__actions {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.detail__cancel-note {
  margin: calc(-1 * var(--cp-space-2)) 0 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.detail__grid {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
  margin: 0;
}

.detail__grid div {
  display: flex;
  gap: var(--cp-space-3);
}

.detail__grid dt {
  width: 72px;
  flex-shrink: 0;
  color: var(--cp-muted);
  font-size: 13px;
}

.detail__grid dd {
  margin: 0;
  color: var(--cp-ink);
  font-size: 13px;
}

.detail__error {
  color: var(--cp-error);
  font-family: var(--cp-font-mono);
}

.detail__answer {
  margin-top: var(--cp-space-4);
  padding-top: var(--cp-space-3);
  border-top: 1px solid var(--cp-hairline-soft);
}

.detail__answer h3 {
  margin: 0 0 var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-muted);
}

.detail__answer p {
  margin: 0;
  color: var(--cp-ink);
  white-space: pre-wrap;
}

.detail__heading {
  margin: 0 0 var(--cp-space-2);
  font-size: 15px;
}

.detail__section {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.detail__step-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.detail__step-seq {
  color: var(--cp-muted-soft);
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.detail__risk {
  font-size: 12px;
  color: var(--cp-warning);
}

.detail__dur {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}
</style>
