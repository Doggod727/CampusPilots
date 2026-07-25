<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { callApi } from '@/api/client'
import { createSensitiveWord, deleteSensitiveWord, listSensitiveWords } from '@/api/generated'
import type { SensitiveWord, SensitiveWordScope } from '@/api/generated'
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

const MATCH_LABEL: Record<SensitiveWord['match_type'], string> = {
  exact: '精确匹配',
  contains: '包含匹配',
  regex: '正则匹配',
}

const ACTION_LABEL: Record<SensitiveWord['action'], string> = {
  mask: '掩码',
  block: '阻断',
  review: '人工复核',
}

const SCOPE_LABEL: Record<SensitiveWordScope, string> = {
  user_input: '用户输入',
  ai_output: 'AI 输出',
  community: '社区内容',
  tool_input: '工具入参',
  tool_output: '工具出参',
  agent_context: 'Agent 上下文',
  all: '全部范围',
}

const SCOPE_FILTERS: Array<{ value: SensitiveWordScope | ''; label: string }> = [
  { value: '', label: '全部范围' },
  { value: 'user_input', label: '用户输入' },
  { value: 'ai_output', label: 'AI 输出' },
  { value: 'community', label: '社区内容' },
  { value: 'tool_input', label: '工具入参' },
  { value: 'tool_output', label: '工具出参' },
  { value: 'agent_context', label: 'Agent 上下文' },
  { value: 'all', label: '全部范围' },
]

const ENABLED_FILTERS = [
  { value: '', label: '全部状态' },
  { value: 'true', label: '启用' },
  { value: 'false', label: '停用' },
] as const

// ---------- 列表 ----------
const keyword = ref('')
const scopeFilter = ref<SensitiveWordScope | ''>('')
const enabledFilter = ref<(typeof ENABLED_FILTERS)[number]['value']>('')

const { items, total, page, pageSize, loading, failed, isEmpty, load, changePage } = useResourceList<SensitiveWord>(
  async (currentPage, size) => {
    const response = await callApi(() =>
      listSensitiveWords({
        query: {
          page: currentPage,
          page_size: size,
          ...(keyword.value.trim() ? { q: keyword.value.trim() } : {}),
          ...(scopeFilter.value ? { scope: scopeFilter.value as SensitiveWordScope } : {}),
          ...(enabledFilter.value === '' ? {} : { enabled: enabledFilter.value === 'true' }),
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

// ---------- 新建规则 ----------
const createOpen = ref(false)
const createForm = reactive({
  word: '',
  match_type: 'contains' as SensitiveWord['match_type'],
  action: 'mask' as SensitiveWord['action'],
  replacement: '',
  scope: 'all' as SensitiveWordScope,
  enabled: true,
})
const createSubmitting = ref(false)
const createFailure = ref<Failure | null>(null)
/** 同一表单会话固定幂等键：重试复用，成功后才轮换。 */
const createKey = ref(crypto.randomUUID())

const canCreate = computed(() => createForm.word.trim().length > 0 && !createSubmitting.value)

function toggleCreate() {
  createOpen.value = !createOpen.value
  createFailure.value = null
}

async function submitCreate() {
  if (!canCreate.value) {
    return
  }
  createSubmitting.value = true
  createFailure.value = null
  try {
    await callApi(() =>
      createSensitiveWord({
        body: {
          word: createForm.word.trim(),
          match_type: createForm.match_type,
          action: createForm.action,
          replacement: createForm.replacement.trim() || null,
          scope: createForm.scope,
          enabled: createForm.enabled,
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createKey.value = crypto.randomUUID()
    createForm.word = ''
    createForm.replacement = ''
    createForm.enabled = true
    createOpen.value = false
    await load()
  } catch (error) {
    createFailure.value = describeApiError(error, '创建敏感词失败', {
      DUPLICATE_RESOURCE: '相同规则的敏感词已存在。',
      INVALID_SENSITIVE_WORD_RULE: '规则无效，请检查正则表达式或替换文本。',
    })
  } finally {
    createSubmitting.value = false
  }
}

// ---------- 删除 ----------
const confirmingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const deleteFailure = ref<Failure | null>(null)

function askDelete(word: SensitiveWord) {
  confirmingId.value = confirmingId.value === word.id ? null : word.id
  deleteFailure.value = null
}

async function confirmDelete(word: SensitiveWord) {
  if (deletingId.value) {
    return
  }
  deletingId.value = word.id
  deleteFailure.value = null
  try {
    await callApi(() => deleteSensitiveWord({ path: { word_id: word.id } }))
    confirmingId.value = null
    await load()
  } catch (error) {
    deleteFailure.value = describeApiError(error, '删除敏感词失败', {
      SENSITIVE_WORD_NOT_FOUND: '该规则已不存在，请刷新列表。',
    })
  } finally {
    deletingId.value = null
  }
}
</script>

<template>
  <div class="words">
    <PageHeader title="敏感词" subtitle="内容安全规则；写操作携带幂等键，重试不会产生重复规则">
      <UiButton variant="primary" @click="toggleCreate">{{ createOpen ? '取消新建' : '新建规则' }}</UiButton>
    </PageHeader>

    <UiCard v-if="createOpen" padding="md" class="words__panel">
      <h2 class="words__panel-title">新建敏感词规则</h2>
      <form class="words__form" @submit.prevent="submitCreate">
        <div class="words__grid">
          <UiField label="敏感词 / 表达式" input-id="word-create-word" required hint="1–200 字；正则匹配时填写表达式">
            <input
              id="word-create-word"
              v-model="createForm.word"
              class="words__input"
              maxlength="200"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="匹配方式" input-id="word-create-match">
            <select id="word-create-match" v-model="createForm.match_type" class="words__input" :disabled="createSubmitting">
              <option v-for="(label, value) in MATCH_LABEL" :key="value" :value="value">{{ label }}</option>
            </select>
          </UiField>
          <UiField label="处置动作" input-id="word-create-action">
            <select id="word-create-action" v-model="createForm.action" class="words__input" :disabled="createSubmitting">
              <option v-for="(label, value) in ACTION_LABEL" :key="value" :value="value">{{ label }}</option>
            </select>
          </UiField>
          <UiField label="生效范围" input-id="word-create-scope">
            <select id="word-create-scope" v-model="createForm.scope" class="words__input" :disabled="createSubmitting">
              <option v-for="(label, value) in SCOPE_LABEL" :key="value" :value="value">{{ label }}</option>
            </select>
          </UiField>
          <UiField label="替换为" input-id="word-create-replacement" hint="动作为掩码时使用；留空则使用默认掩码">
            <input
              id="word-create-replacement"
              v-model="createForm.replacement"
              class="words__input"
              maxlength="100"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="状态" input-id="word-create-enabled">
            <label class="words__check">
              <input id="word-create-enabled" v-model="createForm.enabled" type="checkbox" :disabled="createSubmitting" />
              <span>创建后立即启用</span>
            </label>
          </UiField>
        </div>
        <p v-if="createFailure" class="words__failure" role="alert">
          <strong>{{ createFailure.title }}</strong>
          <span>{{ createFailure.message }}</span>
        </p>
        <div class="words__actions">
          <UiButton variant="primary" type="submit" :loading="createSubmitting" :disabled="!canCreate">创建规则</UiButton>
        </div>
      </form>
    </UiCard>

    <div class="words__toolbar">
      <form class="words__search" @submit.prevent="applyFilters">
        <input v-model="keyword" class="words__input" placeholder="按规则内容搜索" aria-label="搜索敏感词" />
        <UiButton type="submit">搜索</UiButton>
      </form>
      <select v-model="scopeFilter" class="words__input words__select" aria-label="按范围筛选" @change="applyFilters">
        <option v-for="option in SCOPE_FILTERS" :key="option.label" :value="option.value">{{ option.label }}</option>
      </select>
      <select v-model="enabledFilter" class="words__input words__select" aria-label="按状态筛选" @change="applyFilters">
        <option v-for="option in ENABLED_FILTERS" :key="option.label" :value="option.value">{{ option.label }}</option>
      </select>
    </div>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="敏感词列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无敏感词规则" description="调整筛选条件，或新建一条规则" />
    <template v-else>
      <UiCard padding="none" class="words__table-card">
        <table class="words__table">
          <thead>
            <tr>
              <th>规则内容</th>
              <th>匹配方式</th>
              <th>处置动作</th>
              <th>替换为</th>
              <th>生效范围</th>
              <th>状态</th>
              <th>创建时间</th>
              <th class="words__col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="word in items" :key="word.id">
              <tr>
                <td>
                  <span class="words__word">{{ word.word }}</span>
                </td>
                <td>{{ MATCH_LABEL[word.match_type] }}</td>
                <td>{{ ACTION_LABEL[word.action] }}</td>
                <td>{{ word.replacement || '—' }}</td>
                <td>{{ SCOPE_LABEL[word.scope] }}</td>
                <td>
                  <span class="words__state" :class="word.enabled ? 'words__state--on' : 'words__state--off'">
                    {{ word.enabled ? '启用' : '停用' }}
                  </span>
                </td>
                <td>
                  <time class="words__time">{{ formatTime(word.created_at) }}</time>
                </td>
                <td class="words__col-actions">
                  <UiButton size="sm" variant="danger" @click="askDelete(word)">删除</UiButton>
                </td>
              </tr>
              <tr v-if="confirmingId === word.id">
                <td colspan="8" class="words__confirm-cell">
                  <div class="words__confirm" role="alert">
                    <span>确认删除规则「{{ word.word }}」？</span>
                    <UiButton size="sm" variant="danger" :loading="deletingId === word.id" @click="confirmDelete(word)">
                      确认删除
                    </UiButton>
                    <UiButton size="sm" :disabled="deletingId === word.id" @click="confirmingId = null">取消</UiButton>
                    <span v-if="deleteFailure" class="words__inline-error">{{ deleteFailure.message }}</span>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </UiCard>
      <div class="words__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.words {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.words__panel-title {
  margin: 0 0 var(--cp-space-3);
  font-size: 15px;
  color: var(--cp-ink);
}

.words__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.words__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--cp-space-3);
}

.words__input {
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

.words__check {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-2);
  min-height: var(--cp-control-md);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.words__toolbar {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.words__search {
  display: flex;
  gap: var(--cp-space-2);
  flex: 1;
  min-width: 240px;
  max-width: 440px;
}

.words__select {
  width: auto;
  min-width: 140px;
}

.words__table-card {
  overflow-x: auto;
}

.words__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.words__table th,
.words__table td {
  padding: var(--cp-space-2) var(--cp-space-3);
  text-align: left;
  border-bottom: 1px solid var(--cp-hairline-soft);
  vertical-align: middle;
}

.words__table th {
  color: var(--cp-muted);
  font-weight: 500;
  white-space: nowrap;
}

.words__table tbody tr:last-child td {
  border-bottom: none;
}

.words__word {
  font-family: var(--cp-font-mono);
  font-size: 12px;
  color: var(--cp-ink);
  word-break: break-all;
}

.words__state {
  font-size: 12px;
}

.words__state--on {
  color: var(--cp-success);
}

.words__state--off {
  color: var(--cp-muted);
}

.words__time {
  color: var(--cp-muted);
  white-space: nowrap;
}

.words__col-actions {
  text-align: right;
  white-space: nowrap;
}

.words__confirm-cell {
  background: var(--cp-canvas-soft);
}

.words__confirm {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--cp-body-strong);
}

.words__pagination {
  display: flex;
  justify-content: center;
}

.words__actions {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.words__failure {
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

.words__inline-error {
  color: var(--cp-error);
  font-size: 13px;
}
</style>
