<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import { createAgentRun } from '@/api/generated'
import { useAgentCatalogStore } from '@/modules/agent-workbench/stores/catalog'
import { PageHeader, UiButton, UiCard, UiField } from '@/shared/ui'

const MODES = [
  { value: 'auto', label: '自动路由', hint: '由路由器选择最合适的 Agent' },
  { value: 'knowledge', label: '知识问答', hint: '校规/文档类问题' },
  { value: 'service', label: '校园服务', hint: '办事指南/工单/电费' },
  { value: 'community', label: '社区互助', hint: '活动/失物招领' },
  { value: 'governance', label: '平台治理', hint: '审核/权限/审计' },
  { value: 'modelops', label: '模型工程', hint: '数据集/训练/评估' },
] as const

const router = useRouter()
const catalog = useAgentCatalogStore()
const form = reactive({ mode: 'auto' as (typeof MODES)[number]['value'], input: '' })
const submitting = ref(false)
const failure = ref<{ title: string; message: string } | null>(null)
/** 同一次表单会话固定幂等键：重试复用，避免重复创建。 */
const submissionKey = ref(crypto.randomUUID())

const canSubmit = computed(() => form.input.trim().length >= 2 && !submitting.value)

function describeError(error: unknown): { title: string; message: string } {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return { title: '权限不足', message: '当前账号没有创建 Agent Run 的权限。' }
    }
    if (error.status === 409) {
      return { title: '创建冲突', message: '相同请求已处理或目标不可用（Agent/Tool 已停用）。' }
    }
    if (error.status === 422) {
      return { title: '输入无效', message: error.details[0]?.reason ?? '请检查输入内容。' }
    }
    if (error.status === 429) {
      return { title: '请求过于频繁', message: '已达创建上限，请稍后再试。' }
    }
  }
  return { title: '创建失败', message: '服务暂不可用，请稍后重试。' }
}

async function submit() {
  if (!canSubmit.value) {
    return
  }
  submitting.value = true
  failure.value = null
  try {
    const response = await callApi(() =>
      createAgentRun({
        body: { input: form.input.trim(), mode: form.mode, context: {} },
        headers: { 'Idempotency-Key': submissionKey.value },
      }),
    )
    submissionKey.value = crypto.randomUUID()
    form.input = ''
    await router.push({ name: 'agent-run-detail', params: { runId: response.data.id } })
  } catch (error) {
    failure.value = describeError(error)
  } finally {
    submitting.value = false
  }
}

onMounted(() => catalog.load())
</script>

<template>
  <div class="create">
    <PageHeader title="创建 Agent Run" subtitle="选择一个运行模式并描述任务；高风险 Tool 会先进入审批" />

    <UiCard class="create__card" padding="lg">
      <form class="create__form" @submit.prevent="submit">
        <UiField label="运行模式" input-id="run-mode">
          <div class="create__modes" role="radiogroup">
            <label
              v-for="mode in MODES"
              :key="mode.value"
              class="create__mode"
              :class="{ 'create__mode--active': form.mode === mode.value }"
            >
              <input v-model="form.mode" type="radio" name="mode" :value="mode.value" class="sr-only" />
              <strong>{{ mode.label }}</strong>
              <span>{{ mode.hint }}</span>
            </label>
          </div>
        </UiField>

        <UiField label="任务描述" input-id="run-input" required hint="2–4000 字；描述你要完成的任务">
          <textarea
            id="run-input"
            v-model="form.input"
            class="create__input"
            rows="5"
            maxlength="4000"
            placeholder="例如：查询望江校区的地址，并告诉我怎么去图书馆"
            :disabled="submitting"
          />
        </UiField>

        <p v-if="failure" class="create__error" role="alert">
          <strong>{{ failure.title }}</strong>
          <span>{{ failure.message }}</span>
        </p>

        <div class="create__actions">
          <UiButton variant="primary" type="submit" :loading="submitting" :disabled="!canSubmit">创建运行</UiButton>
        </div>
      </form>
    </UiCard>

    <UiCard v-if="catalog.loaded" class="create__catalog" padding="md">
      <h2 class="create__catalog-title">可用 Agent 与 Tool（来自后端目录）</h2>
      <ul class="create__catalog-list">
        <li v-for="agent in catalog.agents.filter((item) => item.enabled)" :key="agent.code">
          <strong>{{ agent.name }}</strong>
          <span class="create__catalog-desc">（{{ agent.tool_allowlist.length }} 个 Tool）</span>
        </li>
      </ul>
    </UiCard>
  </div>
</template>

<style scoped>
.create {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
  max-width: 860px;
}

.create__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.create__modes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--cp-space-2);
}

.create__mode {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-card);
  cursor: pointer;
  background: var(--cp-surface-card);
}

.create__mode strong {
  font-size: 13px;
  color: var(--cp-ink);
}

.create__mode span {
  font-size: 12px;
  color: var(--cp-muted);
}

.create__mode--active {
  border-color: var(--cp-primary);
  background: color-mix(in srgb, var(--cp-primary) 6%, white);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

.create__input {
  width: 100%;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  resize: vertical;
}

.create__error {
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

.create__catalog-title {
  margin: 0 0 var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-muted);
}

.create__catalog-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
</style>
