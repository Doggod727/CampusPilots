<script setup lang="ts">
import { reactive, ref } from 'vue'

import { callApi } from '@/api/client'
import { getAuditLog, listAuditLogs } from '@/api/generated'
import type { AuditLog } from '@/api/generated'
import { useResourceList } from '@/shared/lib/useResourceList'
import {
  EmptyState,
  ErrorState,
  PageHeader,
  UiButton,
  UiCard,
  UiField,
  UiPagination,
  UiSkeleton,
} from '@/shared/ui'

import { describeApiError, formatTime, type Failure } from './admin-utils'
import JsonTree from './JsonTree.vue'

// ---------- 筛选与列表 ----------
const filters = reactive({ action: '', resource_type: '', from: '', to: '' })

/** 日期输入（本地时区）转 ISO 边界；契约 from/to 为 date-time。 */
function toIsoBoundary(value: string, endOfDay: boolean): string | undefined {
  if (!value) {
    return undefined
  }
  const date = new Date(`${value}T${endOfDay ? '23:59:59.999' : '00:00:00.000'}`)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

const { items, total, page, pageSize, loading, failed, isEmpty, load, changePage } = useResourceList<AuditLog>(
  async (currentPage, size) => {
    const fromIso = toIsoBoundary(filters.from, false)
    const toIso = toIsoBoundary(filters.to, true)
    const response = await callApi(() =>
      listAuditLogs({
        query: {
          page: currentPage,
          page_size: size,
          ...(filters.action.trim() ? { action: filters.action.trim() } : {}),
          ...(filters.resource_type.trim() ? { resource_type: filters.resource_type.trim() } : {}),
          ...(fromIso ? { from: fromIso } : {}),
          ...(toIso ? { to: toIso } : {}),
        },
      }),
    )
    return { items: response.data.items, total: response.data.pagination.total }
  },
)

async function applyFilters() {
  page.value = 1
  await load()
}

async function resetFilters() {
  filters.action = ''
  filters.resource_type = ''
  filters.from = ''
  filters.to = ''
  await applyFilters()
}

// ---------- 详情 ----------
const selectedId = ref<string | null>(null)
const detail = ref<AuditLog | null>(null)
const detailLoading = ref(false)
const detailFailed = ref(false)
const detailFailure = ref<Failure | null>(null)

async function openDetail(auditId: string) {
  selectedId.value = auditId
  detailLoading.value = true
  detailFailed.value = false
  detail.value = null
  detailFailure.value = null
  try {
    const response = await callApi(() => getAuditLog({ path: { audit_id: auditId } }))
    detail.value = response.data
  } catch (error) {
    detailFailed.value = true
    detailFailure.value = describeApiError(error, '审计详情加载失败', {
      AUDIT_LOG_NOT_FOUND: '该审计记录不存在或已被清理。',
    })
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  selectedId.value = null
  detail.value = null
}

async function retryDetail() {
  if (selectedId.value) {
    await openDetail(selectedId.value)
  }
}
</script>

<template>
  <div class="audit">
    <PageHeader title="审计日志" subtitle="只读记录；正文已由后端脱敏，疑似密钥字段前端再掩码" />

    <UiCard padding="md" class="audit__panel">
      <form class="audit__filters" @submit.prevent="applyFilters">
        <UiField label="动作" input-id="audit-filter-action">
          <input
            id="audit-filter-action"
            v-model="filters.action"
            class="audit__input"
            placeholder="如 user.update"
          />
        </UiField>
        <UiField label="资源类型" input-id="audit-filter-resource">
          <input
            id="audit-filter-resource"
            v-model="filters.resource_type"
            class="audit__input"
            placeholder="如 user / role"
          />
        </UiField>
        <UiField label="起始日期" input-id="audit-filter-from">
          <input id="audit-filter-from" v-model="filters.from" class="audit__input" type="date" />
        </UiField>
        <UiField label="截止日期" input-id="audit-filter-to">
          <input id="audit-filter-to" v-model="filters.to" class="audit__input" type="date" />
        </UiField>
        <div class="audit__filter-actions">
          <UiButton variant="primary" type="submit">筛选</UiButton>
          <UiButton type="button" @click="resetFilters">重置</UiButton>
        </div>
      </form>
    </UiCard>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="审计日志加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无审计记录" description="调整筛选条件后重试" />
    <template v-else>
      <UiCard padding="none" class="audit__table-card">
        <table class="audit__table">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作者</th>
              <th>动作</th>
              <th>资源</th>
              <th>结果</th>
              <th>请求 ID</th>
              <th class="audit__col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in items" :key="log.id" :class="{ 'audit__row--selected': selectedId === log.id }">
              <td>
                <time class="audit__time">{{ formatTime(log.created_at) }}</time>
              </td>
              <td>{{ log.actor_username || '系统' }}</td>
              <td>
                <code class="audit__mono">{{ log.action }}</code>
              </td>
              <td>{{ log.resource_type }}</td>
              <td>
                <span class="audit__result" :class="`audit__result--${log.result}`">
                  {{ log.result === 'success' ? '成功' : '失败' }}
                </span>
              </td>
              <td>
                <code class="audit__mono audit__request-id" :title="log.request_id">{{ log.request_id }}</code>
              </td>
              <td class="audit__col-actions">
                <UiButton size="sm" @click="openDetail(log.id)">详情</UiButton>
              </td>
            </tr>
          </tbody>
        </table>
      </UiCard>
      <div class="audit__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <UiCard v-if="selectedId" padding="md" class="audit__panel">
      <UiSkeleton v-if="detailLoading" :lines="4" />
      <template v-else-if="detailFailed">
        <ErrorState title="审计详情加载失败" :message="detailFailure?.message ?? ''" @retry="retryDetail" />
      </template>
      <div v-else-if="detail" class="audit__detail">
        <div class="audit__detail-head">
          <h2 class="audit__panel-title">审计详情</h2>
          <span class="audit__result" :class="`audit__result--${detail.result}`">
            {{ detail.result === 'success' ? '成功' : '失败' }}
          </span>
          <UiButton size="sm" class="audit__detail-close" @click="closeDetail">关闭</UiButton>
        </div>

        <dl class="audit__meta">
          <div><dt>时间</dt><dd>{{ formatTime(detail.created_at) }}</dd></div>
          <div><dt>操作者</dt><dd>{{ detail.actor_username || '系统' }}</dd></div>
          <div><dt>动作</dt><dd class="audit__mono">{{ detail.action }}</dd></div>
          <div>
            <dt>资源</dt>
            <dd>{{ detail.resource_type }}<span v-if="detail.resource_id"> / {{ detail.resource_id }}</span></dd>
          </div>
          <div><dt>请求 ID</dt><dd class="audit__mono">{{ detail.request_id }}</dd></div>
          <div v-if="detail.ip_address"><dt>来源 IP</dt><dd class="audit__mono">{{ detail.ip_address }}</dd></div>
          <div v-if="detail.error_code"><dt>错误码</dt><dd class="audit__mono">{{ detail.error_code }}</dd></div>
        </dl>

        <div class="audit__data">
          <section class="audit__section">
            <h3 class="audit__section-title">变更前</h3>
            <JsonTree v-if="detail.before_data" label="before_data" :value="detail.before_data" />
            <p v-else class="audit__empty">无记录</p>
          </section>
          <section class="audit__section">
            <h3 class="audit__section-title">变更后</h3>
            <JsonTree v-if="detail.after_data" label="after_data" :value="detail.after_data" />
            <p v-else class="audit__empty">无记录</p>
          </section>
        </div>
      </div>
    </UiCard>
  </div>
</template>

<style scoped>
.audit {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.audit__panel-title {
  margin: 0;
  font-size: 15px;
  color: var(--cp-ink);
}

.audit__filters {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--cp-space-3);
  align-items: end;
}

.audit__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  background: var(--cp-surface-card);
  color: var(--cp-ink);
  box-sizing: border-box;
}

.audit__filter-actions {
  display: flex;
  gap: var(--cp-space-2);
}

.audit__table-card {
  overflow-x: auto;
}

.audit__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.audit__table th,
.audit__table td {
  padding: var(--cp-space-2) var(--cp-space-3);
  text-align: left;
  border-bottom: 1px solid var(--cp-hairline-soft);
  vertical-align: middle;
}

.audit__table th {
  color: var(--cp-muted);
  font-weight: 500;
  white-space: nowrap;
}

.audit__table tbody tr:last-child td {
  border-bottom: none;
}

.audit__row--selected td {
  background: var(--cp-canvas-soft);
}

.audit__time {
  color: var(--cp-muted);
  white-space: nowrap;
}

.audit__mono {
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.audit__request-id {
  display: inline-block;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  color: var(--cp-muted);
}

.audit__result {
  font-size: 12px;
  font-weight: 500;
}

.audit__result--success {
  color: var(--cp-success);
}

.audit__result--failure {
  color: var(--cp-error);
}

.audit__col-actions {
  text-align: right;
  white-space: nowrap;
}

.audit__pagination {
  display: flex;
  justify-content: center;
}

.audit__detail {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.audit__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
}

.audit__detail-close {
  margin-left: auto;
}

.audit__meta {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
}

.audit__meta dt {
  font-size: 12px;
  color: var(--cp-muted);
}

.audit__meta dd {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--cp-body-strong);
  word-break: break-all;
}

.audit__data {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--cp-space-4);
}

.audit__section {
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  padding: var(--cp-space-3);
  overflow-x: auto;
}

.audit__section-title {
  margin: 0 0 var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-muted);
}

.audit__empty {
  margin: 0;
  font-size: 13px;
  color: var(--cp-muted-soft);
}
</style>
