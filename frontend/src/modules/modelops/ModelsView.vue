<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { callApi } from '@/api/client'
import {
  activateModelVersion,
  compareEvaluations,
  createEvaluation,
  deactivateModelVersion,
  getEvaluation,
  getModelVersion,
  listEvaluations,
  listModelVersions,
  registerModelVersion,
} from '@/api/generated'
import type {
  EvaluationComparison,
  EvaluationJob,
  ModelPurpose,
  ModelVersion,
} from '@/api/generated'
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
const canWriteModel = computed(() => auth.hasPermission('model:write'))
const canActivateModel = computed(() => auth.hasPermission('model:activate'))
const canRunEvaluation = computed(() => auth.hasPermission('evaluation:run'))

const activeTab = ref<'models' | 'evaluations' | 'compare'>('models')

const PURPOSE_OPTIONS: Array<{ value: ModelPurpose; label: string }> = [
  { value: 'complex_generation', label: '复杂生成' },
  { value: 'agent_router', label: '智能体路由' },
  { value: 'rag_reranker', label: 'RAG 重排' },
  { value: 'embedding', label: '向量嵌入' },
]
const PURPOSE_LABELS = Object.fromEntries(PURPOSE_OPTIONS.map((option) => [option.value, option.label])) as Record<
  ModelPurpose,
  string
>

const PROVIDER_OPTIONS: Array<{ value: ModelVersion['provider']; label: string }> = [
  { value: 'deepseek', label: 'DeepSeek（外部 API）' },
  { value: 'local', label: '本地模型' },
  { value: 'rule', label: '规则' },
]
const PROVIDER_LABELS = Object.fromEntries(PROVIDER_OPTIONS.map((option) => [option.value, option.label])) as Record<
  ModelVersion['provider'],
  string
>

const TARGET_TYPE_OPTIONS: Array<{ value: EvaluationJob['target_type']; label: string }> = [
  { value: 'agent', label: 'Agent' },
  { value: 'tool', label: 'Tool' },
  { value: 'model', label: '模型' },
  { value: 'rag', label: 'RAG' },
  { value: 'system', label: '系统' },
]
const TARGET_TYPE_LABELS = Object.fromEntries(TARGET_TYPE_OPTIONS.map((option) => [option.value, option.label])) as Record<
  EvaluationJob['target_type'],
  string
>

// ---------- 模型页 ----------

const purposeFilter = ref<ModelPurpose | ''>('')
const models = ref<ModelVersion[]>([])
const modelsLoading = ref(true)
const modelsFailed = ref(false)

async function loadModels() {
  modelsLoading.value = true
  modelsFailed.value = false
  try {
    const response = await callApi(() =>
      listModelVersions({ query: purposeFilter.value ? { purpose: purposeFilter.value } : {} }),
    )
    models.value = response.data.items
  } catch {
    modelsFailed.value = true
  } finally {
    modelsLoading.value = false
  }
}

async function changePurposeFilter(value: ModelPurpose | '') {
  purposeFilter.value = value
  await loadModels()
}

const registerOpen = ref(false)
const registerForm = ref({
  name: '',
  purpose: 'agent_router' as ModelPurpose,
  provider: 'local' as ModelVersion['provider'],
  base_model: '',
  version: '',
  quantization: '',
  artifact_key: '',
  artifact_sha256: '',
  training_job_id: '',
  config: '',
})
const registerSubmitting = ref(false)
const registerError = ref('')
/** 每次打开注册对话框生成一次幂等键；同一次提交的重试复用。 */
const registerKey = ref('')

const canSubmitRegister = computed(
  () =>
    registerForm.value.name.trim().length >= 2 &&
    registerForm.value.base_model.trim().length >= 2 &&
    registerForm.value.version.trim().length >= 1 &&
    (!registerForm.value.artifact_sha256.trim() || /^[0-9a-f]{64}$/.test(registerForm.value.artifact_sha256.trim())) &&
    !registerSubmitting.value,
)

function openRegister() {
  registerForm.value = {
    name: '',
    purpose: 'agent_router',
    provider: 'local',
    base_model: '',
    version: '',
    quantization: '',
    artifact_key: '',
    artifact_sha256: '',
    training_job_id: '',
    config: '',
  }
  registerError.value = ''
  registerKey.value = crypto.randomUUID()
  registerOpen.value = true
}

async function submitRegister() {
  if (!canSubmitRegister.value) {
    return
  }
  let config: Record<string, unknown> = {}
  const rawConfig = registerForm.value.config.trim()
  if (rawConfig) {
    try {
      const parsed: unknown = JSON.parse(rawConfig)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        registerError.value = 'config 必须是 JSON 对象。'
        return
      }
      config = parsed as Record<string, unknown>
    } catch {
      registerError.value = 'config 不是合法的 JSON。'
      return
    }
  }
  registerSubmitting.value = true
  registerError.value = ''
  try {
    await callApi(() =>
      registerModelVersion({
        body: {
          name: registerForm.value.name.trim(),
          purpose: registerForm.value.purpose,
          provider: registerForm.value.provider,
          base_model: registerForm.value.base_model.trim(),
          version: registerForm.value.version.trim(),
          quantization: registerForm.value.quantization.trim() || null,
          artifact_key: registerForm.value.artifact_key.trim() || null,
          artifact_sha256: registerForm.value.artifact_sha256.trim() || null,
          training_job_id: registerForm.value.training_job_id.trim() || null,
          config,
        },
        headers: { 'Idempotency-Key': registerKey.value },
      }),
    )
    registerOpen.value = false
    await loadModels()
  } catch (error) {
    registerError.value = describeModelOpsError(error, '注册失败，请稍后重试。')
  } finally {
    registerSubmitting.value = false
  }
}

const modelDetailOpen = ref(false)
const modelDetailLoading = ref(false)
const modelDetailError = ref('')
const modelDetailId = ref('')
const modelDetail = ref<ModelVersion | null>(null)

async function refreshModelDetail(modelId: string): Promise<void> {
  modelDetailLoading.value = true
  modelDetailError.value = ''
  try {
    const response = await callApi(() => getModelVersion({ path: { model_id: modelId } }))
    modelDetail.value = response.data
  } catch (error) {
    modelDetailError.value = describeModelOpsError(error, '详情加载失败，请稍后重试。')
  } finally {
    modelDetailLoading.value = false
  }
}

function openModelDetail(model: ModelVersion) {
  modelDetailOpen.value = true
  modelDetailId.value = model.id
  modelDetail.value = null
  void refreshModelDetail(model.id)
}

const stateTarget = ref<{ model: ModelVersion; action: 'activate' | 'deactivate' } | null>(null)
const stateSubmitting = ref(false)
const stateError = ref('')
/** 每次打开激活/停用确认生成一次幂等键；同一次提交的重试复用。 */
const stateKey = ref('')

function openStateChange(model: ModelVersion, action: 'activate' | 'deactivate') {
  stateTarget.value = { model, action }
  stateError.value = ''
  stateKey.value = crypto.randomUUID()
}

async function submitStateChange() {
  const target = stateTarget.value
  if (!target || stateSubmitting.value) {
    return
  }
  stateSubmitting.value = true
  stateError.value = ''
  try {
    const request =
      target.action === 'activate'
        ? activateModelVersion({ path: { model_id: target.model.id }, headers: { 'Idempotency-Key': stateKey.value } })
        : deactivateModelVersion({ path: { model_id: target.model.id }, headers: { 'Idempotency-Key': stateKey.value } })
    await callApi(() => request)
    stateTarget.value = null
    if (modelDetailId.value === target.model.id && modelDetailOpen.value) {
      await refreshModelDetail(target.model.id)
    }
    await loadModels()
  } catch (error) {
    stateError.value = describeModelOpsError(error, '操作失败，请稍后重试。')
  } finally {
    stateSubmitting.value = false
  }
}

// ---------- 评估页 ----------

const {
  items: evaluations,
  total: evaluationsTotal,
  page: evaluationsPage,
  pageSize: evaluationsPageSize,
  loading: evaluationsLoading,
  failed: evaluationsFailed,
  isEmpty: evaluationsEmpty,
  load: loadEvaluations,
  changePage: changeEvaluationsPage,
} = useResourceList<EvaluationJob>(async (currentPage, currentPageSize) => {
  const response = await callApi(() => listEvaluations({ query: { page: currentPage, page_size: currentPageSize } }))
  return { items: response.data.items, total: response.data.pagination.total }
}, 10)

const evalCreateOpen = ref(false)
const evalCreateForm = ref({
  target_type: 'model' as EvaluationJob['target_type'],
  target_id: '',
  dataset_id: '',
  dataset_version: '' as number | '',
  config: '',
})
const evalCreateSubmitting = ref(false)
const evalCreateError = ref('')
/** 每次打开创建评估对话框生成一次幂等键；同一次提交的重试复用。 */
const evalCreateKey = ref('')

const canSubmitEvalCreate = computed(
  () =>
    (evalCreateForm.value.dataset_version === '' ||
      (Number.isInteger(evalCreateForm.value.dataset_version) && evalCreateForm.value.dataset_version >= 1)) &&
    !evalCreateSubmitting.value,
)

function openEvalCreate() {
  evalCreateForm.value = { target_type: 'model', target_id: '', dataset_id: '', dataset_version: '', config: '' }
  evalCreateError.value = ''
  evalCreateKey.value = crypto.randomUUID()
  evalCreateOpen.value = true
}

async function submitEvalCreate() {
  if (!canSubmitEvalCreate.value) {
    return
  }
  let config: Record<string, unknown> = {}
  const rawConfig = evalCreateForm.value.config.trim()
  if (rawConfig) {
    try {
      const parsed: unknown = JSON.parse(rawConfig)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        evalCreateError.value = 'config 必须是 JSON 对象。'
        return
      }
      config = parsed as Record<string, unknown>
    } catch {
      evalCreateError.value = 'config 不是合法的 JSON。'
      return
    }
  }
  evalCreateSubmitting.value = true
  evalCreateError.value = ''
  try {
    await callApi(() =>
      createEvaluation({
        body: {
          target_type: evalCreateForm.value.target_type,
          target_id: evalCreateForm.value.target_id.trim() || null,
          dataset_id: evalCreateForm.value.dataset_id.trim() || null,
          dataset_version: evalCreateForm.value.dataset_version === '' ? null : evalCreateForm.value.dataset_version,
          config,
        },
        headers: { 'Idempotency-Key': evalCreateKey.value },
      }),
    )
    evalCreateOpen.value = false
    await loadEvaluations()
  } catch (error) {
    evalCreateError.value = describeModelOpsError(error, '创建失败，请稍后重试。')
  } finally {
    evalCreateSubmitting.value = false
  }
}

const evalDetailOpen = ref(false)
const evalDetailLoading = ref(false)
const evalDetailError = ref('')
const evalDetailId = ref('')
const evalDetail = ref<EvaluationJob | null>(null)

async function refreshEvalDetail(evaluationId: string): Promise<void> {
  evalDetailLoading.value = true
  evalDetailError.value = ''
  try {
    const response = await callApi(() => getEvaluation({ path: { evaluation_id: evaluationId } }))
    evalDetail.value = response.data
  } catch (error) {
    evalDetailError.value = describeModelOpsError(error, '详情加载失败，请稍后重试。')
  } finally {
    evalDetailLoading.value = false
  }
}

function openEvalDetail(evaluation: EvaluationJob) {
  evalDetailOpen.value = true
  evalDetailId.value = evaluation.id
  evalDetail.value = null
  void refreshEvalDetail(evaluation.id)
}

// ---------- 比较页 ----------

const compareCandidates = ref<EvaluationJob[]>([])
const compareLoading = ref(false)
const compareFailed = ref(false)
const compareLoaded = ref(false)
const compareSelected = ref<string[]>([])
const compareSubmitting = ref(false)
const compareError = ref('')
const compareResult = ref<EvaluationComparison | null>(null)

const canCompare = computed(
  () => compareSelected.value.length >= 2 && compareSelected.value.length <= 5 && !compareSubmitting.value,
)

async function loadCompareCandidates() {
  compareLoading.value = true
  compareFailed.value = false
  try {
    const response = await callApi(() => listEvaluations({ query: { page: 1, page_size: 50 } }))
    compareCandidates.value = response.data.items.filter((item) => item.status === 'succeeded')
    compareLoaded.value = true
  } catch {
    compareFailed.value = true
  } finally {
    compareLoading.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab === 'compare' && !compareLoaded.value && !compareLoading.value) {
    void loadCompareCandidates()
  }
})

function compareDisabled(evaluation: EvaluationJob): boolean {
  return compareSelected.value.length >= 5 && !compareSelected.value.includes(evaluation.id)
}

async function submitCompare() {
  if (!canCompare.value) {
    return
  }
  compareSubmitting.value = true
  compareError.value = ''
  compareResult.value = null
  try {
    const response = await callApi(() => compareEvaluations({ body: { evaluation_ids: [...compareSelected.value] } }))
    compareResult.value = response.data
  } catch (error) {
    compareError.value = describeModelOpsError(error, '比较失败，请稍后重试。')
  } finally {
    compareSubmitting.value = false
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function formatJson(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2)
}

function formatMetricValue(value: unknown): string {
  return typeof value === 'object' && value !== null
    ? formatJson(value as Record<string, unknown>)
    : String(value ?? '—')
}

function hasContent(record: Record<string, unknown>): boolean {
  return Object.keys(record).length > 0
}

function shortId(id: string): string {
  return id.slice(0, 8)
}

loadModels()
</script>

<template>
  <div class="models">
    <PageHeader title="模型与评估" subtitle="模型注册表、真实评估任务与评估横向比较" />

    <el-tabs v-model="activeTab">
      <el-tab-pane label="模型" name="models">
        <div class="models__pane">
          <div class="models__toolbar">
            <div class="models__filters" role="tablist" aria-label="用途筛选">
              <button
                type="button"
                class="models__filter"
                :class="{ 'models__filter--active': purposeFilter === '' }"
                @click="changePurposeFilter('')"
              >
                全部用途
              </button>
              <button
                v-for="option in PURPOSE_OPTIONS"
                :key="option.value"
                type="button"
                class="models__filter"
                :class="{ 'models__filter--active': purposeFilter === option.value }"
                @click="changePurposeFilter(option.value)"
              >
                {{ option.label }}
              </button>
            </div>
            <UiButton v-if="canWriteModel" variant="primary" @click="openRegister">注册模型版本</UiButton>
          </div>

          <UiSkeleton v-if="modelsLoading" :lines="5" />
          <ErrorState v-else-if="modelsFailed" title="模型列表加载失败" @retry="loadModels" />
          <EmptyState v-else-if="models.length === 0" title="暂无模型版本" description="注册模型版本后可在此管理激活状态" />
          <div v-else class="models__list">
            <UiCard v-for="model in models" :key="model.id" class="models__item" padding="md" @click="openModelDetail(model)">
              <div class="models__item-head">
                <StatusBadge :status="model.status" />
                <strong class="models__name">{{ model.name }}</strong>
                <span class="models__version">v{{ model.version }}</span>
                <span class="models__purpose">{{ PURPOSE_LABELS[model.purpose] }}</span>
                <span class="models__provider">{{ PROVIDER_LABELS[model.provider] }}</span>
              </div>
              <p class="models__meta">基座：{{ model.base_model }}<template v-if="model.quantization"> · 量化：{{ model.quantization }}</template></p>
              <div class="models__item-actions">
                <UiButton
                  v-if="canActivateModel && (model.status === 'candidate' || model.status === 'inactive')"
                  variant="text"
                  size="sm"
                  @click.stop="openStateChange(model, 'activate')"
                >
                  激活
                </UiButton>
                <UiButton v-if="canActivateModel && model.status === 'active'" variant="text" size="sm" @click.stop="openStateChange(model, 'deactivate')">
                  停用
                </UiButton>
              </div>
            </UiCard>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="评估" name="evaluations">
        <div class="models__pane">
          <div class="models__toolbar">
            <p class="models__hint">评估指标来自真实执行（RAG/Agent/Tool/模型/系统五类 Provider）</p>
            <UiButton v-if="canRunEvaluation" variant="primary" @click="openEvalCreate">创建评估</UiButton>
          </div>

          <UiSkeleton v-if="evaluationsLoading" :lines="5" />
          <ErrorState v-else-if="evaluationsFailed" title="评估列表加载失败" @retry="loadEvaluations" />
          <EmptyState v-else-if="evaluationsEmpty" title="暂无评估任务" description="创建评估任务后可在此查看指标" />
          <template v-else>
            <div class="models__list">
              <UiCard v-for="evaluation in evaluations" :key="evaluation.id" class="models__item" padding="md" @click="openEvalDetail(evaluation)">
                <div class="models__item-head">
                  <StatusBadge :status="evaluation.status" />
                  <span class="models__purpose">{{ TARGET_TYPE_LABELS[evaluation.target_type] }}</span>
                  <span v-if="evaluation.target_id" class="models__mono">目标 {{ shortId(evaluation.target_id) }}</span>
                  <time class="models__time">{{ formatTime(evaluation.created_at) }}</time>
                </div>
                <p class="models__meta">{{ evaluation.metrics.length }} 项指标</p>
                <p v-if="evaluation.status === 'failed' && evaluation.error_code" class="models__error-line">{{ evaluation.error_code }}</p>
              </UiCard>
            </div>
            <div class="models__pagination">
              <UiPagination :page="evaluationsPage" :total="evaluationsTotal" :page-size="evaluationsPageSize" @change="changeEvaluationsPage" />
            </div>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane label="比较" name="compare">
        <div class="models__pane">
          <p class="models__hint">选择 2–5 个已成功的评估进行同名指标横向比较（不自动激活任何模型）</p>

          <UiSkeleton v-if="compareLoading" :lines="4" />
          <ErrorState v-else-if="compareFailed" title="候选评估加载失败" @retry="loadCompareCandidates" />
          <EmptyState v-else-if="compareCandidates.length === 0" title="暂无可比较的评估" description="只有已成功完成的评估才能参与比较" />
          <template v-else>
            <el-checkbox-group v-model="compareSelected" class="models__compare-list">
              <el-checkbox
                v-for="evaluation in compareCandidates"
                :key="evaluation.id"
                :value="evaluation.id"
                :disabled="compareDisabled(evaluation)"
                class="models__compare-item"
              >
                <span class="models__mono">{{ shortId(evaluation.id) }}</span>
                <span class="models__purpose">{{ TARGET_TYPE_LABELS[evaluation.target_type] }}</span>
                <span class="models__meta-inline">{{ evaluation.metrics.length }} 项指标 · {{ formatTime(evaluation.created_at) }}</span>
              </el-checkbox>
            </el-checkbox-group>
            <div class="models__compare-actions">
              <span class="models__hint">已选 {{ compareSelected.length }}/5</span>
              <UiButton variant="primary" :loading="compareSubmitting" :disabled="!canCompare" @click="submitCompare">比较</UiButton>
            </div>
            <p v-if="compareError" class="models__error" role="alert">{{ compareError }}</p>

            <table v-if="compareResult && compareResult.metric_names.length > 0" class="models__compare-table">
              <thead>
                <tr>
                  <th>评估</th>
                  <th v-for="name in compareResult.metric_names" :key="name">{{ name }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in compareResult.rows" :key="row.evaluation_id">
                  <td class="models__mono">{{ shortId(row.evaluation_id) }}</td>
                  <td v-for="name in compareResult.metric_names" :key="name">
                    {{ row.metrics[name] ?? '—' }}
                  </td>
                </tr>
              </tbody>
            </table>
            <EmptyState v-else-if="compareResult" title="所选评估没有同名指标" description="仅同名指标可横向比较" />
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="registerOpen" title="注册模型版本" width="560px">
      <form class="models__form" @submit.prevent="submitRegister">
        <div class="models__form-row">
          <UiField label="名称" input-id="model-name" required>
            <input id="model-name" v-model="registerForm.name" class="models__input" maxlength="100" :disabled="registerSubmitting" />
          </UiField>
          <UiField label="版本" input-id="model-version" required>
            <input id="model-version" v-model="registerForm.version" class="models__input" maxlength="50" placeholder="例如 1.0.0" :disabled="registerSubmitting" />
          </UiField>
        </div>
        <div class="models__form-row">
          <UiField label="用途" input-id="model-purpose" required>
            <select id="model-purpose" v-model="registerForm.purpose" class="models__input" :disabled="registerSubmitting">
              <option v-for="option in PURPOSE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </UiField>
          <UiField label="提供方" input-id="model-provider" required>
            <select id="model-provider" v-model="registerForm.provider" class="models__input" :disabled="registerSubmitting">
              <option v-for="option in PROVIDER_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </UiField>
        </div>
        <UiField label="基座模型" input-id="model-base" required>
          <input id="model-base" v-model="registerForm.base_model" class="models__input" maxlength="200" :disabled="registerSubmitting" />
        </UiField>
        <div class="models__form-row">
          <UiField label="量化（可选）" input-id="model-quant">
            <input id="model-quant" v-model="registerForm.quantization" class="models__input" maxlength="30" :disabled="registerSubmitting" />
          </UiField>
          <UiField label="训练任务 ID（可选）" input-id="model-job">
            <input id="model-job" v-model="registerForm.training_job_id" class="models__input" :disabled="registerSubmitting" />
          </UiField>
        </div>
        <UiField label="artifact_key（可选）" input-id="model-artifact-key">
          <input id="model-artifact-key" v-model="registerForm.artifact_key" class="models__input" maxlength="500" :disabled="registerSubmitting" />
        </UiField>
        <UiField label="artifact_sha256（可选，64 位小写十六进制）" input-id="model-artifact-sha">
          <input id="model-artifact-sha" v-model="registerForm.artifact_sha256" class="models__input" maxlength="64" :disabled="registerSubmitting" />
        </UiField>
        <UiField label="config（可选，JSON 对象）" input-id="model-config">
          <textarea id="model-config" v-model="registerForm.config" class="models__input" rows="2" :disabled="registerSubmitting" />
        </UiField>
        <p v-if="registerError" class="models__error" role="alert">{{ registerError }}</p>
        <div class="models__dialog-actions">
          <UiButton variant="default" :disabled="registerSubmitting" @click="registerOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="registerSubmitting" :disabled="!canSubmitRegister">注册</UiButton>
        </div>
      </form>
    </el-dialog>

    <el-dialog v-model="modelDetailOpen" title="模型版本详情" width="640px">
      <UiSkeleton v-if="modelDetailLoading" :lines="5" />
      <ErrorState v-else-if="modelDetailError" title="详情加载失败" :message="modelDetailError" @retry="refreshModelDetail(modelDetailId)" />
      <template v-else-if="modelDetail">
        <div class="models__detail-head">
          <StatusBadge :status="modelDetail.status" />
          <strong>{{ modelDetail.name }}</strong>
          <span class="models__version">v{{ modelDetail.version }}</span>
        </div>
        <dl class="models__detail">
          <div><dt>用途</dt><dd>{{ PURPOSE_LABELS[modelDetail.purpose] }}</dd></div>
          <div><dt>提供方</dt><dd>{{ PROVIDER_LABELS[modelDetail.provider] }}</dd></div>
          <div><dt>基座模型</dt><dd>{{ modelDetail.base_model }}</dd></div>
          <div><dt>量化</dt><dd>{{ modelDetail.quantization ?? '—' }}</dd></div>
          <div><dt>注册时间</dt><dd>{{ formatTime(modelDetail.created_at) }}</dd></div>
          <div><dt>激活时间</dt><dd>{{ modelDetail.activated_at ? formatTime(modelDetail.activated_at) : '—' }}</dd></div>
          <div v-if="modelDetail.artifact_sha256" class="models__detail-wide">
            <dt>artifact_sha256</dt>
            <dd class="models__mono">{{ modelDetail.artifact_sha256 }}</dd>
          </div>
        </dl>
        <template v-if="hasContent(modelDetail.metrics)">
          <p class="models__section-title">评估指标</p>
          <table class="models__compare-table">
            <thead>
              <tr><th>指标</th><th>值</th></tr>
            </thead>
            <tbody>
              <tr v-for="(value, name) in modelDetail.metrics" :key="name">
                <td>{{ name }}</td>
                <td>{{ formatMetricValue(value) }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <template v-if="hasContent(modelDetail.config)">
          <p class="models__section-title">配置</p>
          <pre class="models__json">{{ formatJson(modelDetail.config) }}</pre>
        </template>
        <div v-if="canActivateModel" class="models__dialog-actions">
          <UiButton
            v-if="modelDetail.status === 'candidate' || modelDetail.status === 'inactive'"
            variant="primary"
            @click="openStateChange(modelDetail, 'activate')"
          >
            激活
          </UiButton>
          <UiButton v-if="modelDetail.status === 'active'" variant="danger" @click="openStateChange(modelDetail, 'deactivate')">停用</UiButton>
        </div>
      </template>
    </el-dialog>

    <el-dialog :model-value="stateTarget !== null" :title="stateTarget?.action === 'activate' ? '激活模型' : '停用模型'" width="480px" @update:model-value="stateTarget = null">
      <template v-if="stateTarget">
        <p class="models__confirm">
          确认{{ stateTarget.action === 'activate' ? '激活' : '停用' }}
          <span class="models__mono">{{ stateTarget.model.name }} v{{ stateTarget.model.version }}</span>？
          {{ stateTarget.action === 'activate' ? '激活前需通过评估；同用途旧版本将按后端规则处理。' : '停用后该版本不再提供服务。' }}
        </p>
        <p v-if="stateError" class="models__error" role="alert">{{ stateError }}</p>
        <div class="models__dialog-actions">
          <UiButton variant="default" :disabled="stateSubmitting" @click="stateTarget = null">取消</UiButton>
          <UiButton :variant="stateTarget.action === 'activate' ? 'primary' : 'danger'" :loading="stateSubmitting" @click="submitStateChange">
            确认{{ stateTarget.action === 'activate' ? '激活' : '停用' }}
          </UiButton>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="evalCreateOpen" title="创建评估" width="560px">
      <form class="models__form" @submit.prevent="submitEvalCreate">
        <UiField label="评估对象类型" input-id="eval-target-type" required>
          <select id="eval-target-type" v-model="evalCreateForm.target_type" class="models__input" :disabled="evalCreateSubmitting">
            <option v-for="option in TARGET_TYPE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </UiField>
        <UiField label="目标 ID（可选）" input-id="eval-target-id" hint="model/tool 类型时填写目标标识">
          <input id="eval-target-id" v-model="evalCreateForm.target_id" class="models__input" :disabled="evalCreateSubmitting" />
        </UiField>
        <div class="models__form-row">
          <UiField label="数据集 ID（可选）" input-id="eval-dataset">
            <input id="eval-dataset" v-model="evalCreateForm.dataset_id" class="models__input" :disabled="evalCreateSubmitting" />
          </UiField>
          <UiField label="数据集版本（可选）" input-id="eval-dataset-version">
            <input id="eval-dataset-version" v-model.number="evalCreateForm.dataset_version" class="models__input" type="number" min="1" :disabled="evalCreateSubmitting" />
          </UiField>
        </div>
        <UiField label="config（可选，JSON 对象）" input-id="eval-config">
          <textarea id="eval-config" v-model="evalCreateForm.config" class="models__input" rows="2" :disabled="evalCreateSubmitting" />
        </UiField>
        <p v-if="evalCreateError" class="models__error" role="alert">{{ evalCreateError }}</p>
        <div class="models__dialog-actions">
          <UiButton variant="default" :disabled="evalCreateSubmitting" @click="evalCreateOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="evalCreateSubmitting" :disabled="!canSubmitEvalCreate">创建</UiButton>
        </div>
      </form>
    </el-dialog>

    <el-dialog v-model="evalDetailOpen" title="评估详情" width="640px">
      <UiSkeleton v-if="evalDetailLoading" :lines="5" />
      <ErrorState v-else-if="evalDetailError" title="详情加载失败" :message="evalDetailError" @retry="refreshEvalDetail(evalDetailId)" />
      <template v-else-if="evalDetail">
        <div class="models__detail-head">
          <StatusBadge :status="evalDetail.status" />
          <span class="models__purpose">{{ TARGET_TYPE_LABELS[evalDetail.target_type] }}</span>
          <span v-if="evalDetail.target_id" class="models__mono">目标 {{ evalDetail.target_id }}</span>
        </div>
        <div v-if="evalDetail.status === 'failed'" class="models__failed" role="alert">
          <strong>评估失败</strong>
          <span class="models__mono">{{ evalDetail.error_code ?? 'UNKNOWN' }}</span>
          <span>失败的评估不产生指标，也不能用于模型激活。</span>
        </div>
        <dl class="models__detail">
          <div><dt>创建时间</dt><dd>{{ formatTime(evalDetail.created_at) }}</dd></div>
          <div><dt>完成时间</dt><dd>{{ evalDetail.finished_at ? formatTime(evalDetail.finished_at) : '—' }}</dd></div>
          <div v-if="evalDetail.report_key" class="models__detail-wide"><dt>报告键</dt><dd class="models__mono">{{ evalDetail.report_key }}</dd></div>
        </dl>
        <template v-if="evalDetail.metrics.length > 0">
          <p class="models__section-title">指标</p>
          <table class="models__compare-table">
            <thead>
              <tr><th>名称</th><th>切片</th><th>值</th><th>单位</th></tr>
            </thead>
            <tbody>
              <tr v-for="metric in evalDetail.metrics" :key="`${metric.name}:${metric.slice_name}`">
                <td>{{ metric.name }}</td>
                <td>{{ metric.slice_name }}</td>
                <td>{{ metric.value }}</td>
                <td>{{ metric.unit ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <template v-if="hasContent(evalDetail.summary)">
          <p class="models__section-title">摘要</p>
          <pre class="models__json">{{ formatJson(evalDetail.summary) }}</pre>
        </template>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.models {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.models__pane {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
  padding-top: var(--cp-space-3);
}

.models__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.models__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.models__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.models__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.models__hint {
  margin: 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.models__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.models__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.models__item:hover {
  border-color: var(--cp-muted);
}

.models__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.models__name {
  font-size: 14px;
  color: var(--cp-ink);
}

.models__version {
  font-size: 12px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.models__purpose {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.models__provider {
  font-size: 12px;
  color: var(--cp-muted);
}

.models__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.models__meta {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.models__meta-inline {
  font-size: 12px;
  color: var(--cp-muted);
}

.models__item-actions {
  display: flex;
  justify-content: flex-end;
}

.models__pagination {
  display: flex;
  justify-content: center;
}

.models__error-line {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-error);
  font-size: 12px;
  font-family: var(--cp-font-mono);
}

.models__compare-list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.models__compare-item {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.models__compare-actions {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
}

.models__compare-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.models__compare-table th {
  text-align: left;
  padding: var(--cp-space-2);
  color: var(--cp-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--cp-hairline);
  white-space: nowrap;
}

.models__compare-table td {
  padding: var(--cp-space-2);
  border-bottom: 1px solid var(--cp-hairline-soft);
}

.models__compare-table tr:last-child td {
  border-bottom: none;
}

.models__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.models__form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--cp-space-3);
}

.models__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  box-sizing: border-box;
}

textarea.models__input {
  resize: vertical;
}

.models__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  margin-bottom: var(--cp-space-3);
}

.models__detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
  margin: 0;
}

.models__detail dt {
  font-size: 12px;
  color: var(--cp-muted);
}

.models__detail dd {
  margin: 0;
  font-size: 13px;
  color: var(--cp-ink);
  word-break: break-all;
}

.models__detail-wide {
  grid-column: 1 / -1;
}

.models__section-title {
  margin: var(--cp-space-4) 0 var(--cp-space-1);
  font-size: 13px;
  color: var(--cp-muted);
}

.models__json {
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

.models__failed {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: var(--cp-space-3);
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.models__mono {
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.models__confirm {
  margin: 0;
  font-size: 14px;
  color: var(--cp-body);
}

.models__error {
  margin: var(--cp-space-2) 0 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.models__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-4);
}
</style>
