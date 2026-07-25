<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { callApi } from '@/api/client'
import { listAgentRuns } from '@/api/generated'
import type { AgentRun, AgentRunStatus } from '@/api/generated'
import { EmptyState, ErrorState, PageHeader, StatusBadge, UiButton, UiCard, UiPagination, UiSkeleton } from '@/shared/ui'

const items = ref<AgentRun[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const loading = ref(true)
const failed = ref(false)

const STATUS_FILTERS: Array<{ value: AgentRunStatus | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'running', label: '运行中' },
  { value: 'awaiting_approval', label: '待审批' },
  { value: 'succeeded', label: '成功' },
  { value: 'partial', label: '部分完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]
const statusFilter = ref<AgentRunStatus | ''>('')

async function load() {
  loading.value = true
  failed.value = false
  try {
    const response = await callApi(() =>
      listAgentRuns({
        query: {
          page: page.value,
          page_size: pageSize,
          ...(statusFilter.value ? { status: statusFilter.value as AgentRunStatus } : {}),
        },
      }),
    )
    items.value = response.data.items
    total.value = response.data.pagination.total
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function changePage(next: number) {
  page.value = next
  await load()
}

async function changeFilter(value: AgentRunStatus | '') {
  statusFilter.value = value
  page.value = 1
  await load()
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<template>
  <div class="runs">
    <PageHeader title="Agent 工作台" subtitle="你的 Agent Run 列表（实时状态以后端为准）">
      <UiButton variant="primary" @click="$router.push({ name: 'agent-run-new' })">创建运行</UiButton>
    </PageHeader>

    <div class="runs__filters" role="tablist">
      <button
        v-for="filter in STATUS_FILTERS"
        :key="filter.label"
        type="button"
        class="runs__filter"
        :class="{ 'runs__filter--active': statusFilter === filter.value }"
        @click="changeFilter(filter.value)"
      >
        {{ filter.label }}
      </button>
    </div>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="列表加载失败" @retry="load" />
    <EmptyState v-else-if="items.length === 0" title="暂无运行记录" description="创建一个 Agent Run 试试" />
    <template v-else>
      <div class="runs__list">
        <UiCard
          v-for="run in items"
          :key="run.id"
          class="runs__item"
          padding="md"
          @click="$router.push({ name: 'agent-run-detail', params: { runId: run.id } })"
        >
          <div class="runs__item-head">
            <StatusBadge :status="run.status" />
            <span class="runs__route">{{ run.route ?? 'auto' }}</span>
            <time class="runs__time">{{ formatTime(run.created_at) }}</time>
          </div>
          <p class="runs__summary">{{ run.input_summary }}</p>
          <p v-if="run.final_answer" class="runs__answer">{{ run.final_answer.slice(0, 120) }}</p>
          <p v-else-if="run.error_code" class="runs__error">{{ run.error_code }}</p>
        </UiCard>
      </div>
      <div class="runs__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.runs {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.runs__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.runs__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.runs__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.runs__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.runs__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.runs__item:hover {
  border-color: var(--cp-muted);
}

.runs__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.runs__route {
  font-size: 12px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.runs__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.runs__summary {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-ink);
  font-size: 14px;
}

.runs__answer {
  margin: var(--cp-space-1) 0 0;
  color: var(--cp-muted);
  font-size: 13px;
}

.runs__error {
  margin: var(--cp-space-1) 0 0;
  color: var(--cp-error);
  font-size: 12px;
  font-family: var(--cp-font-mono);
}

.runs__pagination {
  display: flex;
  justify-content: center;
}
</style>
