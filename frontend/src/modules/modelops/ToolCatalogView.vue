<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { callApi } from '@/api/client'
import { getTool, listTools, updateToolRuntimeState } from '@/api/generated'
import type { ToolCatalogItem } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import {
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  UiButton,
  UiCard,
  UiPagination,
  UiSkeleton,
} from '@/shared/ui'

import { describeModelOpsError } from './errors'

const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('tool:catalog:write'))

const MODULE_FILTERS = [
  { value: '', label: '全部模块' },
  { value: 'm1', label: 'M1 知识库' },
  { value: 'm2', label: 'M2 校园服务' },
  { value: 'm3', label: 'M3 社区互助' },
  { value: 'm4', label: 'M4 平台治理' },
  { value: 'm5', label: 'M5 模型工程' },
] as const
type ModuleFilter = (typeof MODULE_FILTERS)[number]['value']

const STATE_FILTERS = [
  { value: '', label: '全部状态' },
  { value: 'enabled', label: '已启用' },
  { value: 'disabled', label: '已停用' },
] as const
type StateFilter = (typeof STATE_FILTERS)[number]['value']

const RISK_META: Record<ToolCatalogItem['risk_level'], { label: string; tone: string }> = {
  r0: { label: 'R0 低', tone: 'tools__risk--r0' },
  r1: { label: 'R1 中', tone: 'tools__risk--r1' },
  r2: { label: 'R2 高', tone: 'tools__risk--r2' },
  r3: { label: 'R3 极高', tone: 'tools__risk--r3' },
}

const MODULE_LABELS: Record<ToolCatalogItem['module'], string> = {
  m1: 'M1',
  m2: 'M2',
  m3: 'M3',
  m4: 'M4',
  m5: 'M5',
}

const items = ref<ToolCatalogItem[]>([])
const loading = ref(true)
const failed = ref(false)
const moduleFilter = ref<ModuleFilter>('')
const stateFilter = ref<StateFilter>('')
const page = ref(1)
const pageSize = 10

/** 契约 listTools 无分页参数：整目录取回后在前端分页。 */
const pagedItems = computed(() => items.value.slice((page.value - 1) * pageSize, page.value * pageSize))

async function load() {
  loading.value = true
  failed.value = false
  try {
    const response = await callApi(() =>
      listTools({
        query: {
          ...(moduleFilter.value ? { module: moduleFilter.value } : {}),
          ...(stateFilter.value ? { enabled: stateFilter.value === 'enabled' } : {}),
        },
      }),
    )
    items.value = response.data.items
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function changeModuleFilter(value: ModuleFilter) {
  moduleFilter.value = value
  page.value = 1
  await load()
}

async function changeStateFilter(value: StateFilter) {
  stateFilter.value = value
  page.value = 1
  await load()
}

function changePage(next: number) {
  page.value = next
}

const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detail = ref<ToolCatalogItem | null>(null)

async function openDetail(tool: ToolCatalogItem) {
  detailOpen.value = true
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  try {
    const response = await callApi(() => getTool({ path: { tool_name: tool.name } }))
    detail.value = response.data
  } catch (error) {
    detailError.value = describeModelOpsError(error, '详情加载失败，请稍后重试。')
  } finally {
    detailLoading.value = false
  }
}

const toggleOpen = ref(false)
const toggleTarget = ref<ToolCatalogItem | null>(null)
const toggleReason = ref('')
const toggleSubmitting = ref(false)
const toggleError = ref('')
/** 每次打开启停对话框生成一次幂等键；同一次提交的重试复用该键。 */
const toggleKey = ref('')

const canSubmitToggle = computed(() => toggleReason.value.trim().length >= 2 && !toggleSubmitting.value)

function openToggle(tool: ToolCatalogItem) {
  toggleTarget.value = tool
  toggleReason.value = ''
  toggleError.value = ''
  toggleKey.value = crypto.randomUUID()
  toggleOpen.value = true
}

async function submitToggle() {
  const target = toggleTarget.value
  if (!target || !canSubmitToggle.value) {
    return
  }
  toggleSubmitting.value = true
  toggleError.value = ''
  try {
    await callApi(() =>
      updateToolRuntimeState({
        path: { tool_name: target.name },
        body: { enabled: !target.enabled, confirmed: true, reason: toggleReason.value.trim() },
        headers: { 'Idempotency-Key': toggleKey.value },
      }),
    )
    toggleOpen.value = false
    await load()
  } catch (error) {
    toggleError.value = describeModelOpsError(error, '状态更新失败，请稍后重试。')
  } finally {
    toggleSubmitting.value = false
  }
}

function formatJson(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2)
}

onMounted(load)
</script>

<template>
  <div class="tools">
    <PageHeader title="Tool 目录" subtitle="运行时 Tool 契约、风险等级与启停状态（数据来自后端目录）" />

    <div class="tools__filters">
      <div class="tools__filter-group" role="tablist" aria-label="模块筛选">
        <button
          v-for="filter in MODULE_FILTERS"
          :key="filter.value"
          type="button"
          class="tools__filter"
          :class="{ 'tools__filter--active': moduleFilter === filter.value }"
          @click="changeModuleFilter(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
      <div class="tools__filter-group" role="tablist" aria-label="状态筛选">
        <button
          v-for="filter in STATE_FILTERS"
          :key="filter.value"
          type="button"
          class="tools__filter"
          :class="{ 'tools__filter--active': stateFilter === filter.value }"
          @click="changeStateFilter(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
    </div>

    <UiSkeleton v-if="loading" :lines="6" />
    <ErrorState v-else-if="failed" title="目录加载失败" @retry="load" />
    <EmptyState v-else-if="items.length === 0" title="暂无 Tool" description="当前筛选条件下没有 Tool" />
    <template v-else>
      <UiCard padding="none" class="tools__table-card">
        <table class="tools__table">
          <thead>
            <tr>
              <th>名称</th>
              <th>版本</th>
              <th>描述</th>
              <th>风险等级</th>
              <th>需审批</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="tool in pagedItems" :key="tool.name">
              <td>
                <span class="tools__name">{{ tool.name }}</span>
                <span class="tools__module">{{ MODULE_LABELS[tool.module] }}</span>
              </td>
              <td class="tools__version">{{ tool.version }}</td>
              <td class="tools__desc">{{ tool.description }}</td>
              <td>
                <span class="tools__risk" :class="RISK_META[tool.risk_level].tone">{{ RISK_META[tool.risk_level].label }}</span>
              </td>
              <td>{{ tool.requires_approval ? '是' : '否' }}</td>
              <td><StatusBadge :status="tool.enabled ? 'active' : 'inactive'" /></td>
              <td>
                <div class="tools__actions">
                  <UiButton variant="text" size="sm" @click="openDetail(tool)">详情</UiButton>
                  <UiButton v-if="canManage" variant="text" size="sm" @click="openToggle(tool)">
                    {{ tool.enabled ? '停用' : '启用' }}
                  </UiButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </UiCard>
      <div class="tools__pagination">
        <UiPagination :page="page" :total="items.length" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <el-dialog v-model="detailOpen" title="Tool 详情" width="640px">
      <UiSkeleton v-if="detailLoading" :lines="5" />
      <p v-else-if="detailError" class="tools__error" role="alert">{{ detailError }}</p>
      <template v-else-if="detail">
        <dl class="tools__detail">
          <div><dt>名称</dt><dd class="tools__mono">{{ detail.name }}</dd></div>
          <div><dt>模块</dt><dd>{{ MODULE_LABELS[detail.module] }}</dd></div>
          <div><dt>版本</dt><dd>{{ detail.version }}</dd></div>
          <div><dt>风险等级</dt><dd>{{ RISK_META[detail.risk_level].label }}</dd></div>
          <div><dt>需审批</dt><dd>{{ detail.requires_approval ? '是' : '否' }}</dd></div>
          <div><dt>幂等</dt><dd>{{ detail.idempotent ? '是' : '否' }}</dd></div>
          <div><dt>超时</dt><dd>{{ detail.timeout_ms }} ms</dd></div>
          <div><dt>调用权限</dt><dd>{{ detail.required_permissions.join('、') || '无' }}</dd></div>
        </dl>
        <p class="tools__schema-title">输入 Schema</p>
        <pre class="tools__schema">{{ formatJson(detail.input_schema) }}</pre>
        <p class="tools__schema-title">输出 Schema</p>
        <pre class="tools__schema">{{ formatJson(detail.output_schema) }}</pre>
      </template>
    </el-dialog>

    <el-dialog
      v-model="toggleOpen"
      :title="toggleTarget ? (toggleTarget.enabled ? '停用 Tool' : '启用 Tool') : '变更状态'"
      width="520px"
    >
      <template v-if="toggleTarget">
        <p class="tools__toggle-summary">
          确认{{ toggleTarget.enabled ? '停用' : '启用' }}
          <span class="tools__mono">{{ toggleTarget.name }}</span>
          ？{{ toggleTarget.enabled ? '停用后运行时将拒绝新的调用。' : '启用后运行时恢复调用。' }}
        </p>
        <label class="tools__toggle-label" for="toggle-reason">变更原因（必填）</label>
        <textarea
          id="toggle-reason"
          v-model="toggleReason"
          class="tools__toggle-input"
          rows="3"
          maxlength="200"
          placeholder="例如：上游数据源维护，临时停用"
          :disabled="toggleSubmitting"
        />
        <p v-if="toggleError" class="tools__error" role="alert">{{ toggleError }}</p>
        <div class="tools__dialog-actions">
          <UiButton variant="default" :disabled="toggleSubmitting" @click="toggleOpen = false">取消</UiButton>
          <UiButton
            :variant="toggleTarget.enabled ? 'danger' : 'primary'"
            :loading="toggleSubmitting"
            :disabled="!canSubmitToggle"
            @click="submitToggle"
          >
            确认{{ toggleTarget.enabled ? '停用' : '启用' }}
          </UiButton>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tools {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.tools__filters {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.tools__filter-group {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.tools__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.tools__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.tools__table-card {
  overflow-x: auto;
}

.tools__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.tools__table th {
  text-align: left;
  padding: var(--cp-space-3) var(--cp-space-4);
  color: var(--cp-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--cp-hairline);
  white-space: nowrap;
}

.tools__table td {
  padding: var(--cp-space-3) var(--cp-space-4);
  border-bottom: 1px solid var(--cp-hairline-soft);
  vertical-align: top;
}

.tools__table tr:last-child td {
  border-bottom: none;
}

.tools__name {
  display: block;
  font-family: var(--cp-font-mono);
  font-size: 12px;
  color: var(--cp-ink);
}

.tools__module {
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.tools__version {
  font-family: var(--cp-font-mono);
  font-size: 12px;
  color: var(--cp-body);
  white-space: nowrap;
}

.tools__desc {
  color: var(--cp-body);
  max-width: 320px;
}

.tools__risk {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--cp-radius-pill);
  font-size: 12px;
  white-space: nowrap;
  border: 1px solid var(--cp-hairline);
}

.tools__risk--r0 {
  color: var(--cp-success);
  border-color: color-mix(in srgb, var(--cp-success) 35%, transparent);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
}

.tools__risk--r1 {
  color: var(--cp-info);
  border-color: color-mix(in srgb, var(--cp-info) 35%, transparent);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
}

.tools__risk--r2 {
  color: var(--cp-warning);
  border-color: color-mix(in srgb, var(--cp-warning) 35%, transparent);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
}

.tools__risk--r3 {
  color: var(--cp-error);
  border-color: color-mix(in srgb, var(--cp-error) 35%, transparent);
  background: color-mix(in srgb, var(--cp-error) 7%, white);
}

.tools__actions {
  display: flex;
  gap: var(--cp-space-1);
  white-space: nowrap;
}

.tools__pagination {
  display: flex;
  justify-content: center;
}

.tools__mono {
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.tools__detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
  margin: 0 0 var(--cp-space-4);
}

.tools__detail dt {
  font-size: 12px;
  color: var(--cp-muted);
}

.tools__detail dd {
  margin: 0;
  font-size: 13px;
  color: var(--cp-ink);
}

.tools__schema-title {
  margin: var(--cp-space-3) 0 var(--cp-space-1);
  font-size: 13px;
  color: var(--cp-muted);
}

.tools__schema {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas-soft);
  font-family: var(--cp-font-mono);
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.tools__toggle-summary {
  margin: 0 0 var(--cp-space-3);
  font-size: 14px;
  color: var(--cp-body);
}

.tools__toggle-label {
  display: block;
  margin-bottom: var(--cp-space-1);
  font-size: 13px;
  font-weight: 500;
  color: var(--cp-ink);
}

.tools__toggle-input {
  width: 100%;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  resize: vertical;
  box-sizing: border-box;
}

.tools__error {
  margin: var(--cp-space-3) 0 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.tools__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-4);
}
</style>
