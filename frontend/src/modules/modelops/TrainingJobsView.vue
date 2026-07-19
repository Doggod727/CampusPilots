<script setup lang="ts">
import { computed, ref } from 'vue'

import { callApi } from '@/api/client'
import { cancelTrainingJob, createTrainingJob, getTrainingJob, listTrainingJobs } from '@/api/generated'
import type { TrainingJob } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { useResourceList } from '@/shared/lib/useResourceList'
import {
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  UiButton,
  UiCard,
  UiField,
  UiPagination,
  UiSkeleton,
} from '@/shared/ui'

import { describeModelOpsError } from './errors'

const auth = useAuthStore()
const canRun = computed(() => auth.hasPermission('training:run'))

const CANCELLABLE: ReadonlySet<TrainingJob['status']> = new Set(['queued', 'preparing', 'training', 'evaluating'])

const METHOD_OPTIONS = [
  { value: 'lora', label: 'LoRA', hint: 'CPU/GPU 均可' },
  { value: 'qlora', label: 'QLoRA', hint: '需 CUDA + bitsandbytes' },
] as const

const { items, total, page, pageSize, loading, failed, isEmpty, load, changePage } = useResourceList<TrainingJob>(
  async (currentPage, currentPageSize) => {
    const response = await callApi(() => listTrainingJobs({ query: { page: currentPage, page_size: currentPageSize } }))
    return { items: response.data.items, total: response.data.pagination.total }
  },
  10,
)

const createOpen = ref(false)
const createForm = ref({
  dataset_id: '',
  dataset_version: 1,
  base_model: '',
  method: 'lora' as 'lora' | 'qlora',
  epochs: 1,
  learning_rate: 0.0001,
  batch_size: 4,
  resource_limits: '',
})
const createSubmitting = ref(false)
const createError = ref('')
/** 每次打开创建对话框生成一次幂等键；同一次提交的重试复用。 */
const createKey = ref('')

const canSubmitCreate = computed(
  () =>
    createForm.value.dataset_id.trim().length > 0 &&
    Number.isInteger(createForm.value.dataset_version) &&
    createForm.value.dataset_version >= 1 &&
    createForm.value.base_model.trim().length >= 2 &&
    Number.isInteger(createForm.value.epochs) &&
    createForm.value.epochs >= 1 &&
    createForm.value.learning_rate > 0 &&
    Number.isInteger(createForm.value.batch_size) &&
    createForm.value.batch_size >= 1 &&
    !createSubmitting.value,
)

function openCreate() {
  createForm.value = {
    dataset_id: '',
    dataset_version: 1,
    base_model: '',
    method: 'lora',
    epochs: 1,
    learning_rate: 0.0001,
    batch_size: 4,
    resource_limits: '',
  }
  createError.value = ''
  createKey.value = crypto.randomUUID()
  createOpen.value = true
}

async function submitCreate() {
  if (!canSubmitCreate.value) {
    return
  }
  let resourceLimits: Record<string, unknown> | undefined
  const rawLimits = createForm.value.resource_limits.trim()
  if (rawLimits) {
    try {
      const parsed: unknown = JSON.parse(rawLimits)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        createError.value = 'resource_limits 必须是 JSON 对象。'
        return
      }
      resourceLimits = parsed as Record<string, unknown>
    } catch {
      createError.value = 'resource_limits 不是合法的 JSON。'
      return
    }
  }
  createSubmitting.value = true
  createError.value = ''
  try {
    await callApi(() =>
      createTrainingJob({
        body: {
          dataset_id: createForm.value.dataset_id.trim(),
          dataset_version: createForm.value.dataset_version,
          base_model: createForm.value.base_model.trim(),
          method: createForm.value.method,
          config: {
            epochs: createForm.value.epochs,
            learning_rate: createForm.value.learning_rate,
            batch_size: createForm.value.batch_size,
          },
          ...(resourceLimits ? { resource_limits: resourceLimits } : {}),
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createOpen.value = false
    await load()
  } catch (error) {
    createError.value = describeModelOpsError(error, '创建失败，请稍后重试。')
  } finally {
    createSubmitting.value = false
  }
}

const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detailId = ref('')
const detail = ref<TrainingJob | null>(null)

async function refreshDetail(jobId: string): Promise<void> {
  detailLoading.value = true
  detailError.value = ''
  try {
    const response = await callApi(() => getTrainingJob({ path: { training_job_id: jobId } }))
    detail.value = response.data
  } catch (error) {
    detailError.value = describeModelOpsError(error, '详情加载失败，请稍后重试。')
  } finally {
    detailLoading.value = false
  }
}

function openDetail(job: TrainingJob) {
  detailOpen.value = true
  detailId.value = job.id
  detail.value = null
  void refreshDetail(job.id)
}

const cancelTarget = ref<TrainingJob | null>(null)
const cancelSubmitting = ref(false)
const cancelError = ref('')
/** 每次打开取消确认生成一次幂等键；同一次提交的重试复用。 */
const cancelKey = ref('')

function openCancel(job: TrainingJob) {
  cancelTarget.value = job
  cancelError.value = ''
  cancelKey.value = crypto.randomUUID()
}

async function submitCancel() {
  const target = cancelTarget.value
  if (!target || cancelSubmitting.value) {
    return
  }
  cancelSubmitting.value = true
  cancelError.value = ''
  try {
    await callApi(() =>
      cancelTrainingJob({ path: { training_job_id: target.id }, headers: { 'Idempotency-Key': cancelKey.value } }),
    )
    cancelTarget.value = null
    if (detailId.value === target.id) {
      await refreshDetail(target.id)
    }
    await load()
  } catch (error) {
    cancelError.value = describeModelOpsError(error, '取消失败，请稍后重试。')
  } finally {
    cancelSubmitting.value = false
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function formatJson(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2)
}

function hasMetrics(metrics: Record<string, unknown>): boolean {
  return Object.keys(metrics).length > 0
}
</script>

<template>
  <div class="training">
    <PageHeader title="训练任务" subtitle="本地小模型训练（LoRA/QLoRA），状态以后端 Worker 执行结果为准">
      <UiButton v-if="canRun" variant="primary" @click="openCreate">创建训练任务</UiButton>
    </PageHeader>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无训练任务" description="创建训练任务后可在此跟踪进度" />
    <template v-else>
      <div class="training__list">
        <UiCard v-for="job in items" :key="job.id" class="training__item" padding="md" @click="openDetail(job)">
          <div class="training__item-head">
            <StatusBadge :status="job.status" />
            <span class="training__model">{{ job.base_model }}</span>
            <span class="training__method">{{ job.method.toUpperCase() }}</span>
            <time class="training__time">{{ formatTime(job.created_at) }}</time>
          </div>
          <div class="training__progress-row">
            <el-progress :percentage="job.progress" :stroke-width="8" class="training__progress" />
          </div>
          <p v-if="job.status === 'failed' && job.error_code" class="training__error-line">
            {{ job.error_code }}<template v-if="job.error_message">：{{ job.error_message }}</template>
          </p>
          <div class="training__item-actions">
            <UiButton v-if="canRun && CANCELLABLE.has(job.status)" variant="text" size="sm" @click.stop="openCancel(job)">取消任务</UiButton>
          </div>
        </UiCard>
      </div>
      <div class="training__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <el-dialog v-model="createOpen" title="创建训练任务" width="560px">
      <form class="training__form" @submit.prevent="submitCreate">
        <div class="training__form-row">
          <UiField label="数据集 ID" input-id="job-dataset" required hint="已冻结数据集所属 ID">
            <input id="job-dataset" v-model="createForm.dataset_id" class="training__input" :disabled="createSubmitting" />
          </UiField>
          <UiField label="数据集版本" input-id="job-version" required>
            <input id="job-version" v-model.number="createForm.dataset_version" class="training__input" type="number" min="1" :disabled="createSubmitting" />
          </UiField>
        </div>
        <UiField label="基座模型" input-id="job-base" required hint="须在后端允许清单内">
          <input id="job-base" v-model="createForm.base_model" class="training__input" maxlength="200" placeholder="例如 Qwen2.5-0.5B" :disabled="createSubmitting" />
        </UiField>
        <UiField label="训练方法" input-id="job-method">
          <div class="training__methods" role="radiogroup">
            <label
              v-for="option in METHOD_OPTIONS"
              :key="option.value"
              class="training__method-option"
              :class="{ 'training__method-option--active': createForm.method === option.value }"
            >
              <input v-model="createForm.method" type="radio" name="method" :value="option.value" class="training__sr-only" :disabled="createSubmitting" />
              <strong>{{ option.label }}</strong>
              <span>{{ option.hint }}</span>
            </label>
          </div>
        </UiField>
        <div class="training__form-row training__form-row--three">
          <UiField label="epochs" input-id="job-epochs" required>
            <input id="job-epochs" v-model.number="createForm.epochs" class="training__input" type="number" min="1" :disabled="createSubmitting" />
          </UiField>
          <UiField label="learning_rate" input-id="job-lr" required>
            <input id="job-lr" v-model.number="createForm.learning_rate" class="training__input" type="number" step="0.000001" min="0" :disabled="createSubmitting" />
          </UiField>
          <UiField label="batch_size" input-id="job-batch" required>
            <input id="job-batch" v-model.number="createForm.batch_size" class="training__input" type="number" min="1" :disabled="createSubmitting" />
          </UiField>
        </div>
        <UiField label="resource_limits（可选，JSON 对象）" input-id="job-limits">
          <textarea id="job-limits" v-model="createForm.resource_limits" class="training__input" rows="2" placeholder="{&quot;max_minutes&quot;: 60}" :disabled="createSubmitting" />
        </UiField>
        <p v-if="createError" class="training__error" role="alert">{{ createError }}</p>
        <div class="training__dialog-actions">
          <UiButton variant="default" :disabled="createSubmitting" @click="createOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="createSubmitting" :disabled="!canSubmitCreate">创建</UiButton>
        </div>
      </form>
    </el-dialog>

    <el-dialog v-model="detailOpen" title="训练任务详情" width="640px">
      <UiSkeleton v-if="detailLoading" :lines="5" />
      <ErrorState v-else-if="detailError" title="详情加载失败" :message="detailError" @retry="refreshDetail(detailId)" />
      <template v-else-if="detail">
        <div class="training__detail-head">
          <StatusBadge :status="detail.status" />
          <span class="training__model">{{ detail.base_model }}</span>
          <span class="training__method">{{ detail.method.toUpperCase() }}</span>
        </div>
        <el-progress :percentage="detail.progress" :stroke-width="8" class="training__progress" />
        <div v-if="detail.status === 'failed'" class="training__failed" role="alert">
          <strong>训练失败</strong>
          <span class="training__mono">{{ detail.error_code ?? 'UNKNOWN' }}</span>
          <span v-if="detail.error_message">{{ detail.error_message }}</span>
        </div>
        <dl class="training__detail">
          <div><dt>创建时间</dt><dd>{{ formatTime(detail.created_at) }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ formatTime(detail.updated_at) }}</dd></div>
          <div><dt>开始时间</dt><dd>{{ detail.started_at ? formatTime(detail.started_at) : '—' }}</dd></div>
          <div><dt>结束时间</dt><dd>{{ detail.finished_at ? formatTime(detail.finished_at) : '—' }}</dd></div>
        </dl>
        <template v-if="hasMetrics(detail.metrics)">
          <p class="training__section-title">指标</p>
          <pre class="training__json">{{ formatJson(detail.metrics) }}</pre>
        </template>
        <div v-if="canRun && CANCELLABLE.has(detail.status)" class="training__dialog-actions">
          <UiButton variant="danger" @click="openCancel(detail)">取消任务</UiButton>
        </div>
      </template>
    </el-dialog>

    <el-dialog :model-value="cancelTarget !== null" title="取消训练任务" width="480px" @update:model-value="cancelTarget = null">
      <template v-if="cancelTarget">
        <p class="training__confirm">
          确认取消 <span class="training__mono">{{ cancelTarget.base_model }}</span>（{{ cancelTarget.method.toUpperCase() }}）训练？取消是幂等操作，重复提交返回同一结果。
        </p>
        <p v-if="cancelError" class="training__error" role="alert">{{ cancelError }}</p>
        <div class="training__dialog-actions">
          <UiButton variant="default" :disabled="cancelSubmitting" @click="cancelTarget = null">返回</UiButton>
          <UiButton variant="danger" :loading="cancelSubmitting" @click="submitCancel">确认取消</UiButton>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.training {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.training__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.training__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.training__item:hover {
  border-color: var(--cp-muted);
}

.training__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.training__model {
  font-size: 14px;
  color: var(--cp-ink);
  font-weight: 500;
}

.training__method {
  font-size: 12px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.training__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.training__progress-row {
  margin-top: var(--cp-space-2);
}

.training__progress {
  max-width: 480px;
}

.training__error-line {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-error);
  font-size: 12px;
  font-family: var(--cp-font-mono);
}

.training__item-actions {
  display: flex;
  justify-content: flex-end;
}

.training__pagination {
  display: flex;
  justify-content: center;
}

.training__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.training__form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--cp-space-3);
}

.training__form-row--three {
  grid-template-columns: 1fr 1fr 1fr;
}

.training__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  box-sizing: border-box;
}

textarea.training__input {
  resize: vertical;
}

.training__methods {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cp-space-2);
}

.training__method-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-card);
  cursor: pointer;
  background: var(--cp-surface-card);
}

.training__method-option strong {
  font-size: 13px;
  color: var(--cp-ink);
}

.training__method-option span {
  font-size: 12px;
  color: var(--cp-muted);
}

.training__method-option--active {
  border-color: var(--cp-primary);
  background: color-mix(in srgb, var(--cp-primary) 6%, white);
}

.training__sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

.training__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  margin-bottom: var(--cp-space-3);
}

.training__failed {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: var(--cp-space-3) 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.training__detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
  margin: var(--cp-space-3) 0 0;
}

.training__detail dt {
  font-size: 12px;
  color: var(--cp-muted);
}

.training__detail dd {
  margin: 0;
  font-size: 13px;
  color: var(--cp-ink);
}

.training__section-title {
  margin: var(--cp-space-4) 0 var(--cp-space-1);
  font-size: 13px;
  color: var(--cp-muted);
}

.training__json {
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

.training__mono {
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.training__confirm {
  margin: 0;
  font-size: 14px;
  color: var(--cp-body);
}

.training__error {
  margin: var(--cp-space-2) 0 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.training__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-4);
}
</style>
