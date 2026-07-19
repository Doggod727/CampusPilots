<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { callApi } from '@/api/client'
import { listConfigs, updateConfig } from '@/api/generated'
import type { AppConfig } from '@/api/generated'
import { EmptyState, ErrorState, PageHeader, UiButton, UiCard, UiField, UiSkeleton } from '@/shared/ui'

import { describeApiError, formatTime, type Failure } from './admin-utils'

const TYPE_LABEL: Record<AppConfig['value_type'], string> = {
  string: '字符串',
  integer: '整数',
  number: '数值',
  boolean: '布尔',
  json: 'JSON',
}

// ---------- 列表 ----------
const items = ref<AppConfig[]>([])
const loading = ref(true)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    const response = await callApi(() => listConfigs())
    items.value = response.data.items
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ---------- 命名空间筛选（基于已加载数据） ----------
const activeNamespace = ref('')

const namespaces = computed(() => [...new Set(items.value.map((item) => item.namespace))].sort())

const visibleItems = computed(() =>
  activeNamespace.value ? items.value.filter((item) => item.namespace === activeNamespace.value) : items.value,
)

function displayValue(config: AppConfig): string {
  if (config.value === null || config.value === undefined) {
    return '—'
  }
  if (typeof config.value === 'object') {
    return JSON.stringify(config.value)
  }
  return String(config.value)
}

// ---------- 编辑 ----------
const editingKey = ref<string | null>(null)
const editRaw = ref('')
const editError = ref('')
const submitting = ref(false)
const failure = ref<Failure | null>(null)
const notice = ref('')

function editingConfig(): AppConfig | undefined {
  return items.value.find((item) => item.key === editingKey.value)
}

function startEdit(config: AppConfig) {
  if (!config.editable) {
    return
  }
  editingKey.value = config.key
  editRaw.value =
    config.value === null || config.value === undefined
      ? ''
      : typeof config.value === 'object'
        ? JSON.stringify(config.value, null, 2)
        : String(config.value)
  editError.value = ''
  failure.value = null
  notice.value = ''
}

function cancelEdit() {
  editingKey.value = null
  editError.value = ''
  failure.value = null
}

/** 按契约 value_type 解析输入；失败返回 undefined 并填充提示。 */
function parseValue(config: AppConfig): { ok: true; value: unknown } | { ok: false; message: string } {
  const raw = editRaw.value.trim()
  switch (config.value_type) {
    case 'string':
      return { ok: true, value: editRaw.value }
    case 'integer': {
      const parsed = Number(raw)
      return Number.isInteger(parsed)
        ? { ok: true, value: parsed }
        : { ok: false, message: '请输入合法的整数。' }
    }
    case 'number': {
      const parsed = Number(raw)
      return raw !== '' && Number.isFinite(parsed)
        ? { ok: true, value: parsed }
        : { ok: false, message: '请输入合法的数值。' }
    }
    case 'boolean':
      return { ok: true, value: raw === 'true' }
    case 'json':
      try {
        return { ok: true, value: JSON.parse(raw) as unknown }
      } catch {
        return { ok: false, message: 'JSON 格式无效，请检查后重试。' }
      }
  }
}

async function submitEdit() {
  const current = editingConfig()
  if (!current || !current.editable || submitting.value) {
    return
  }
  const parsed = parseValue(current)
  if (!parsed.ok) {
    editError.value = parsed.message
    return
  }
  editError.value = ''
  submitting.value = true
  failure.value = null
  notice.value = ''
  try {
    const response = await callApi(() =>
      updateConfig({
        path: { config_key: current.key },
        body: { value: parsed.value, version: current.version },
      }),
    )
    const index = items.value.findIndex((item) => item.key === current.key)
    if (index >= 0) {
      items.value.splice(index, 1, response.data)
    }
    editingKey.value = null
    notice.value = `配置「${response.data.key}」已保存。`
  } catch (error) {
    failure.value = describeApiError(error, '保存配置失败', {
      CONFIG_NOT_EDITABLE: '该配置不允许在线修改。',
      CONFIG_NOT_FOUND: '配置不存在，请刷新列表。',
      INVALID_CONFIG_VALUE: '配置值类型无效，请核对后重试。',
      RESOURCE_VERSION_CONFLICT: '数据已被其他操作更新，请刷新列表后重试。',
    })
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="config">
    <PageHeader title="业务配置" subtitle="仅 editable 配置可在线修改；敏感值由后端脱敏展示" />

    <div v-if="namespaces.length > 1" class="config__filters" role="tablist" aria-label="按命名空间筛选">
      <button
        type="button"
        class="config__filter"
        :class="{ 'config__filter--active': activeNamespace === '' }"
        @click="activeNamespace = ''"
      >
        全部
      </button>
      <button
        v-for="namespace in namespaces"
        :key="namespace"
        type="button"
        class="config__filter"
        :class="{ 'config__filter--active': activeNamespace === namespace }"
        @click="activeNamespace = namespace"
      >
        {{ namespace }}
      </button>
    </div>

    <p v-if="notice" class="config__notice" role="status">{{ notice }}</p>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="配置列表加载失败" @retry="load" />
    <EmptyState v-else-if="visibleItems.length === 0" title="暂无可展示的配置" description="后端未返回该命名空间下的配置" />
    <div v-else class="config__list">
      <UiCard v-for="config in visibleItems" :key="config.key" padding="md" class="config__item">
        <div class="config__item-head">
          <code class="config__key">{{ config.key }}</code>
          <span class="config__tag">{{ config.namespace }}</span>
          <span class="config__tag config__tag--type">{{ TYPE_LABEL[config.value_type] }}</span>
          <span v-if="!config.editable" class="config__tag config__tag--readonly">只读</span>
          <div class="config__item-actions">
            <UiButton v-if="config.editable" size="sm" @click="editingKey === config.key ? cancelEdit() : startEdit(config)">
              {{ editingKey === config.key ? '取消' : '编辑' }}
            </UiButton>
          </div>
        </div>
        <p v-if="config.description" class="config__desc">{{ config.description }}</p>
        <p class="config__value">
          <code>{{ displayValue(config) }}</code>
        </p>
        <p class="config__meta">
          更新于 {{ formatTime(config.updated_at) }}<span v-if="config.updated_by"> · 由 {{ config.updated_by }}</span> ·
          版本 {{ config.version }}
        </p>

        <form v-if="editingKey === config.key" class="config__editor" @submit.prevent="submitEdit">
          <UiField
            v-if="config.value_type === 'boolean'"
            label="配置值"
            input-id="config-edit-boolean"
          >
            <select id="config-edit-boolean" v-model="editRaw" class="config__input" :disabled="submitting">
              <option value="true">true（开启）</option>
              <option value="false">false（关闭）</option>
            </select>
          </UiField>
          <UiField
            v-else-if="config.value_type === 'json'"
            label="配置值（JSON）"
            input-id="config-edit-json"
            :error="editError"
          >
            <textarea
              id="config-edit-json"
              v-model="editRaw"
              class="config__textarea"
              rows="6"
              :disabled="submitting"
            />
          </UiField>
          <UiField
            v-else
            :label="`配置值（${TYPE_LABEL[config.value_type]}）`"
            input-id="config-edit-value"
            :error="editError"
          >
            <input
              id="config-edit-value"
              v-model="editRaw"
              class="config__input"
              :inputmode="config.value_type === 'string' ? 'text' : 'decimal'"
              :disabled="submitting"
            />
          </UiField>
          <p v-if="failure" class="config__failure" role="alert">
            <strong>{{ failure.title }}</strong>
            <span>{{ failure.message }}</span>
          </p>
          <div class="config__actions">
            <UiButton variant="primary" type="submit" :loading="submitting">保存配置</UiButton>
            <UiButton type="button" :disabled="submitting" @click="cancelEdit">取消</UiButton>
          </div>
        </form>
      </UiCard>
    </div>
  </div>
</template>

<style scoped>
.config {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
  max-width: 960px;
}

.config__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.config__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.config__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.config__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.config__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.config__key {
  font-family: var(--cp-font-mono);
  font-size: 13px;
  color: var(--cp-ink);
  word-break: break-all;
}

.config__tag {
  padding: 2px 10px;
  border-radius: var(--cp-radius-pill);
  border: 1px solid var(--cp-hairline);
  background: var(--cp-canvas-soft);
  color: var(--cp-muted);
  font-size: 12px;
  white-space: nowrap;
}

.config__tag--type {
  color: var(--cp-info);
  border-color: color-mix(in srgb, var(--cp-info) 35%, transparent);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
}

.config__tag--readonly {
  color: var(--cp-warning);
  border-color: color-mix(in srgb, var(--cp-warning) 35%, transparent);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
}

.config__item-actions {
  margin-left: auto;
}

.config__desc {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-body);
}

.config__value {
  margin: var(--cp-space-2) 0 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-soft);
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas-soft);
}

.config__value code {
  font-family: var(--cp-font-mono);
  font-size: 12px;
  color: var(--cp-body-strong);
  word-break: break-all;
  white-space: pre-wrap;
}

.config__meta {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.config__editor {
  margin-top: var(--cp-space-3);
  border-top: 1px solid var(--cp-hairline-soft);
  padding-top: var(--cp-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.config__input {
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

.config__textarea {
  width: 100%;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-mono);
  font-size: 13px;
  resize: vertical;
  box-sizing: border-box;
}

.config__actions {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.config__failure {
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

.config__notice {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-success) 6%, white);
  color: var(--cp-success);
  font-size: 13px;
}
</style>
