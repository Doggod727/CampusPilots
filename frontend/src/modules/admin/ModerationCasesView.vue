<script setup lang="ts">
import { computed, ref } from 'vue'

import { callApi, isApiError } from '@/api/client'
import { decideModerationCase, getModerationCase, listModerationCases } from '@/api/generated'
import type { ModerationCase, ModerationStatus, RiskLevel } from '@/api/generated'
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

type CaseModule = ModerationCase['target_module']
type Decision = 'approved' | 'rejected' | 'escalated'

const STATUS_LABEL: Record<ModerationStatus, string> = {
  pending: '待处理',
  approved: '已批准',
  rejected: '已拒绝',
  escalated: '已升级',
}

const RISK_LABEL: Record<RiskLevel, string> = { low: '低风险', medium: '中风险', high: '高风险', critical: '严重' }

const MODULE_LABEL: Record<CaseModule, string> = {
  ai_knowledge: 'AI 知识库',
  campus_service: '校园服务',
  community: '社区',
  agent_platform: '智能体平台',
}

const DECISION_LABEL: Record<Decision, string> = { approved: '批准', rejected: '拒绝', escalated: '升级' }

const STATUS_FILTERS: Array<{ value: ModerationStatus | ''; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'approved', label: '已批准' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'escalated', label: '已升级' },
]

const RISK_FILTERS: Array<{ value: RiskLevel | ''; label: string }> = [
  { value: '', label: '全部风险' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
  { value: 'critical', label: '严重' },
]

const MODULE_FILTERS: Array<{ value: CaseModule | ''; label: string }> = [
  { value: '', label: '全部模块' },
  { value: 'ai_knowledge', label: 'AI 知识库' },
  { value: 'campus_service', label: '校园服务' },
  { value: 'community', label: '社区' },
  { value: 'agent_platform', label: '智能体平台' },
]

// ---------- 列表 ----------
const statusFilter = ref<ModerationStatus | ''>('pending')
const riskFilter = ref<RiskLevel | ''>('')
const moduleFilter = ref<CaseModule | ''>('')

const { items, total, page, pageSize, loading, failed, isEmpty, load, changePage } = useResourceList<ModerationCase>(
  async (currentPage, size) => {
    const response = await callApi(() =>
      listModerationCases({
        query: {
          page: currentPage,
          page_size: size,
          sort: '-created_at',
          ...(statusFilter.value ? { status: statusFilter.value as ModerationStatus } : {}),
          ...(riskFilter.value ? { risk_level: riskFilter.value as RiskLevel } : {}),
          ...(moduleFilter.value ? { target_module: moduleFilter.value as CaseModule } : {}),
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

async function changeStatusFilter(value: ModerationStatus | '') {
  statusFilter.value = value
  await applyFilters()
}

async function changeRiskFilter(value: RiskLevel | '') {
  riskFilter.value = value
  await applyFilters()
}

async function changeModuleFilter(value: CaseModule | '') {
  moduleFilter.value = value
  await applyFilters()
}

// ---------- 详情 ----------
const selectedId = ref<string | null>(null)
const detail = ref<ModerationCase | null>(null)
const detailLoading = ref(false)
const detailFailed = ref(false)

const decision = ref<Decision>('approved')
const reason = ref('')
const decideSubmitting = ref(false)
const decideFailure = ref<Failure | null>(null)
/** 409 后刷新详情时向审核员保留的提示（不随详情刷新消失）。 */
const refreshNotice = ref('')
/** 同一案件会话固定幂等键：重试复用，成功或关闭后轮换。 */
const decideKey = ref(crypto.randomUUID())

const canDecide = computed(
  () => detail.value?.status === 'pending' && reason.value.trim().length >= 2 && !decideSubmitting.value,
)

async function openDetail(caseId: string) {
  selectedId.value = caseId
  detailLoading.value = true
  detailFailed.value = false
  detail.value = null
  decideFailure.value = null
  decision.value = 'approved'
  reason.value = ''
  decideKey.value = crypto.randomUUID()
  try {
    const response = await callApi(() => getModerationCase({ path: { case_id: caseId } }))
    detail.value = response.data
  } catch {
    detailFailed.value = true
  } finally {
    detailLoading.value = false
  }
}

function selectCase(caseId: string) {
  refreshNotice.value = ''
  void openDetail(caseId)
}

function closeDetail() {
  selectedId.value = null
  detail.value = null
  refreshNotice.value = ''
}

async function retryDetail() {
  if (selectedId.value) {
    await openDetail(selectedId.value)
  }
}

async function submitDecision() {
  const current = detail.value
  if (!current || !canDecide.value) {
    return
  }
  decideSubmitting.value = true
  decideFailure.value = null
  try {
    const response = await callApi(() =>
      decideModerationCase({
        path: { case_id: current.id },
        body: { decision: decision.value, reason: reason.value.trim(), version: current.version },
        headers: { 'Idempotency-Key': decideKey.value },
      }),
    )
    detail.value = response.data
    decideKey.value = crypto.randomUUID()
    reason.value = ''
    await load()
  } catch (error) {
    decideFailure.value = describeApiError(error, '提交决定失败', {
      MODERATION_CASE_ALREADY_DECIDED: '该案件已被其他审核员处理，正在刷新最新状态。',
      RESOURCE_VERSION_CONFLICT: '案件数据已被更新，正在刷新最新状态。',
    })
    if (isApiError(error) && error.status === 409) {
      // 案件已终结或版本落后：刷新详情呈现最终状态，并保留一条提示
      await retryDetail()
      refreshNotice.value = '该案件已处理完成或数据已更新，已为你刷新到最新状态。'
    }
  } finally {
    decideSubmitting.value = false
  }
}
</script>

<template>
  <div class="cases">
    <PageHeader title="审核案件" subtitle="待处理案件优先展示；决定提交后不可撤销" />

    <div class="cases__toolbar">
      <div class="cases__filters" role="tablist" aria-label="按状态筛选">
        <button
          v-for="filter in STATUS_FILTERS"
          :key="filter.label"
          type="button"
          class="cases__filter"
          :class="{ 'cases__filter--active': statusFilter === filter.value }"
          @click="changeStatusFilter(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
      <div class="cases__filters" role="tablist" aria-label="按风险筛选">
        <button
          v-for="filter in RISK_FILTERS"
          :key="filter.label"
          type="button"
          class="cases__filter"
          :class="{ 'cases__filter--active': riskFilter === filter.value }"
          @click="changeRiskFilter(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
      <div class="cases__filters" role="tablist" aria-label="按模块筛选">
        <button
          v-for="filter in MODULE_FILTERS"
          :key="filter.label"
          type="button"
          class="cases__filter"
          :class="{ 'cases__filter--active': moduleFilter === filter.value }"
          @click="changeModuleFilter(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
    </div>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="案件列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无案件" description="当前筛选条件下没有审核案件" />
    <template v-else>
      <div class="cases__list">
        <UiCard
          v-for="item in items"
          :key="item.id"
          class="cases__item"
          padding="md"
          @click="selectCase(item.id)"
        >
          <div class="cases__item-head">
            <span class="cases__risk" :class="`cases__risk--${item.risk_level}`">{{ RISK_LABEL[item.risk_level] }}</span>
            <span class="cases__status" :class="`cases__status--${item.status}`">{{ STATUS_LABEL[item.status] }}</span>
            <span class="cases__module">{{ MODULE_LABEL[item.target_module] }} · {{ item.target_type }}</span>
            <time class="cases__time">{{ formatTime(item.created_at) }}</time>
          </div>
          <p class="cases__excerpt">{{ item.content_excerpt }}</p>
          <p class="cases__hits">命中规则 {{ item.rule_hits.length }} 条</p>
        </UiCard>
      </div>
      <div class="cases__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <UiCard v-if="selectedId" padding="md" class="cases__panel">
      <UiSkeleton v-if="detailLoading" :lines="4" />
      <ErrorState v-else-if="detailFailed" title="案件详情加载失败" @retry="retryDetail" />
      <div v-else-if="detail" class="cases__detail">
        <div class="cases__detail-head">
          <h2 class="cases__panel-title">案件详情</h2>
          <span class="cases__risk" :class="`cases__risk--${detail.risk_level}`">{{ RISK_LABEL[detail.risk_level] }}</span>
          <span class="cases__status" :class="`cases__status--${detail.status}`">{{ STATUS_LABEL[detail.status] }}</span>
          <UiButton size="sm" class="cases__detail-close" @click="closeDetail">关闭</UiButton>
        </div>
        <p v-if="refreshNotice" class="cases__notice" role="status">{{ refreshNotice }}</p>

        <dl class="cases__meta">
          <div><dt>来源模块</dt><dd>{{ MODULE_LABEL[detail.target_module] }} / {{ detail.target_type }}</dd></div>
          <div><dt>目标 ID</dt><dd class="cases__mono">{{ detail.target_id }}</dd></div>
          <div><dt>提交人</dt><dd>{{ detail.submitted_by || '系统' }}</dd></div>
          <div><dt>提交时间</dt><dd>{{ formatTime(detail.created_at) }}</dd></div>
          <div v-if="detail.reviewed_at"><dt>处理时间</dt><dd>{{ formatTime(detail.reviewed_at) }}</dd></div>
          <div v-if="detail.decision_reason"><dt>处理理由</dt><dd>{{ detail.decision_reason }}</dd></div>
        </dl>

        <section class="cases__section">
          <h3 class="cases__section-title">敏感正文（仅供审核，不支持复制导出）</h3>
          <pre class="cases__content">{{ detail.content_excerpt }}</pre>
        </section>

        <section class="cases__section">
          <h3 class="cases__section-title">命中规则</h3>
          <table class="cases__hits-table">
            <thead>
              <tr><th>规则</th><th>动作</th><th>命中片段</th></tr>
            </thead>
            <tbody>
              <tr v-for="(hit, index) in detail.rule_hits" :key="index">
                <td>{{ hit.rule }}</td>
                <td>{{ hit.action }}</td>
                <td class="cases__mono">{{ hit.matched_text || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="detail.status === 'pending'" class="cases__section">
          <h3 class="cases__section-title">处理决定</h3>
          <form class="cases__form" @submit.prevent="submitDecision">
            <div class="cases__decisions" role="radiogroup" aria-label="处理决定">
              <label v-for="(label, value) in DECISION_LABEL" :key="value" class="cases__decision">
                <input v-model="decision" type="radio" name="decision" :value="value" :disabled="decideSubmitting" />
                <span>{{ label }}</span>
              </label>
            </div>
            <UiField label="处理理由" input-id="case-decision-reason" required hint="2–500 字，将写入审核记录">
              <textarea
                id="case-decision-reason"
                v-model="reason"
                class="cases__textarea"
                rows="3"
                maxlength="500"
                :disabled="decideSubmitting"
              />
            </UiField>
            <p v-if="decideFailure" class="cases__failure" role="alert">
              <strong>{{ decideFailure.title }}</strong>
              <span>{{ decideFailure.message }}</span>
            </p>
            <div class="cases__actions">
              <UiButton variant="primary" type="submit" :loading="decideSubmitting" :disabled="!canDecide">
                提交决定
              </UiButton>
            </div>
          </form>
        </section>
      </div>
    </UiCard>
  </div>
</template>

<style scoped>
.cases {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.cases__toolbar {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.cases__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.cases__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.cases__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.cases__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.cases__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.cases__item:hover {
  border-color: var(--cp-muted);
}

.cases__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.cases__risk,
.cases__status {
  padding: 2px 10px;
  border-radius: var(--cp-radius-pill);
  border: 1px solid var(--cp-hairline);
  font-size: 12px;
  font-weight: 500;
  line-height: 20px;
  white-space: nowrap;
}

.cases__risk--low {
  color: var(--cp-muted);
  background: var(--cp-canvas-soft);
}

.cases__risk--medium {
  color: var(--cp-info);
  border-color: color-mix(in srgb, var(--cp-info) 35%, transparent);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
}

.cases__risk--high {
  color: var(--cp-warning);
  border-color: color-mix(in srgb, var(--cp-warning) 35%, transparent);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
}

.cases__risk--critical {
  color: var(--cp-error);
  border-color: color-mix(in srgb, var(--cp-error) 35%, transparent);
  background: color-mix(in srgb, var(--cp-error) 7%, white);
}

.cases__status--pending {
  color: var(--cp-warning);
  border-color: color-mix(in srgb, var(--cp-warning) 35%, transparent);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
}

.cases__status--approved {
  color: var(--cp-success);
  border-color: color-mix(in srgb, var(--cp-success) 35%, transparent);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
}

.cases__status--rejected {
  color: var(--cp-error);
  border-color: color-mix(in srgb, var(--cp-error) 35%, transparent);
  background: color-mix(in srgb, var(--cp-error) 7%, white);
}

.cases__status--escalated {
  color: var(--cp-info);
  border-color: color-mix(in srgb, var(--cp-info) 35%, transparent);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
}

.cases__module {
  font-size: 12px;
  color: var(--cp-muted);
}

.cases__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.cases__excerpt {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-ink);
  font-size: 14px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.cases__hits {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.cases__pagination {
  display: flex;
  justify-content: center;
}

.cases__panel-title {
  margin: 0;
  font-size: 15px;
  color: var(--cp-ink);
}

.cases__detail {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.cases__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
}

.cases__detail-close {
  margin-left: auto;
}

.cases__meta {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
}

.cases__meta dt {
  font-size: 12px;
  color: var(--cp-muted);
}

.cases__meta dd {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--cp-body-strong);
}

.cases__mono {
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.cases__section {
  border-top: 1px solid var(--cp-hairline-soft);
  padding-top: var(--cp-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.cases__section-title {
  margin: 0;
  font-size: 14px;
  color: var(--cp-ink);
}

.cases__content {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas-soft);
  color: var(--cp-body-strong);
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
}

.cases__hits-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.cases__hits-table th,
.cases__hits-table td {
  padding: var(--cp-space-2) var(--cp-space-3);
  text-align: left;
  border-bottom: 1px solid var(--cp-hairline-soft);
}

.cases__hits-table th {
  color: var(--cp-muted);
  font-weight: 500;
}

.cases__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.cases__decisions {
  display: flex;
  gap: var(--cp-space-4);
}

.cases__decision {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.cases__textarea {
  width: 100%;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  resize: vertical;
  box-sizing: border-box;
}

.cases__actions {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.cases__failure {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.cases__notice {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
  color: var(--cp-info);
  font-size: 13px;
}
</style>
