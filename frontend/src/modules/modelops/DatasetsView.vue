<script setup lang="ts">
import { computed, ref } from 'vue'

import { callApi } from '@/api/client'
import {
  createDataset,
  createDatasetVersion,
  deleteDataset,
  freezeDatasetVersion,
  getDataset,
  listDatasets,
  uploadDatasetArtifact,
} from '@/api/generated'
import type { Dataset, DatasetPurpose, DatasetUpload, DatasetVersion } from '@/api/generated'
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
const canWrite = computed(() => auth.hasPermission('dataset:write'))

const PURPOSE_OPTIONS: Array<{ value: DatasetPurpose; label: string }> = [
  { value: 'agent_router', label: '智能体路由' },
  { value: 'instruction_tuning', label: '指令微调' },
  { value: 'rag_reranker', label: 'RAG 重排' },
  { value: 'evaluation', label: '评估' },
]

const PURPOSE_LABELS = Object.fromEntries(PURPOSE_OPTIONS.map((option) => [option.value, option.label])) as Record<
  DatasetPurpose,
  string
>

const { items, total, page, pageSize, loading, failed, isEmpty, load, changePage } = useResourceList<Dataset>(
  async (currentPage, currentPageSize) => {
    const response = await callApi(() => listDatasets({ query: { page: currentPage, page_size: currentPageSize } }))
    return { items: response.data.items, total: response.data.pagination.total }
  },
  10,
)

const createOpen = ref(false)
const createForm = ref<{ name: string; purpose: DatasetPurpose; description: string }>({
  name: '',
  purpose: 'instruction_tuning',
  description: '',
})
const createSubmitting = ref(false)
const createError = ref('')
/** 每次打开创建对话框生成一次幂等键；同一次提交的重试复用。 */
const createKey = ref('')

const canSubmitCreate = computed(() => createForm.value.name.trim().length >= 2 && !createSubmitting.value)

function openCreate() {
  createForm.value = { name: '', purpose: 'instruction_tuning', description: '' }
  createError.value = ''
  createKey.value = crypto.randomUUID()
  createOpen.value = true
}

async function submitCreate() {
  if (!canSubmitCreate.value) {
    return
  }
  createSubmitting.value = true
  createError.value = ''
  try {
    await callApi(() =>
      createDataset({
        body: {
          name: createForm.value.name.trim(),
          purpose: createForm.value.purpose,
          description: createForm.value.description.trim() || null,
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
const detailDataset = ref<Dataset | null>(null)
const detailVersions = ref<DatasetVersion[]>([])

async function refreshDetail(datasetId: string): Promise<boolean> {
  detailLoading.value = true
  detailError.value = ''
  try {
    const response = await callApi(() => getDataset({ path: { dataset_id: datasetId } }))
    detailDataset.value = response.data.dataset
    detailVersions.value = response.data.versions
    return true
  } catch (error) {
    detailError.value = describeModelOpsError(error, '详情加载失败，请稍后重试。')
    return false
  } finally {
    detailLoading.value = false
  }
}

function openDetail(dataset: Dataset) {
  detailOpen.value = true
  detailId.value = dataset.id
  detailDataset.value = null
  detailVersions.value = []
  resetUpload()
  versionOpen.value = false
  void refreshDetail(dataset.id)
}

const uploadFile = ref<File | null>(null)
const uploading = ref(false)
const uploadError = ref('')
const uploaded = ref<DatasetUpload | null>(null)
/** 每次选择新文件生成一次上传幂等键；该文件的重试复用。 */
const uploadKey = ref('')

function resetUpload() {
  uploadFile.value = null
  uploading.value = false
  uploadError.value = ''
  uploaded.value = null
  uploadKey.value = ''
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  uploadFile.value = file
  uploadError.value = ''
  uploaded.value = null
  uploadKey.value = file ? crypto.randomUUID() : ''
}

async function submitUpload() {
  const dataset = detailDataset.value
  const file = uploadFile.value
  if (!dataset || !file || uploading.value) {
    return
  }
  uploading.value = true
  uploadError.value = ''
  try {
    const response = await callApi(() =>
      uploadDatasetArtifact({
        path: { dataset_id: dataset.id },
        body: { file },
        headers: { 'Idempotency-Key': uploadKey.value },
      }),
    )
    uploaded.value = response.data
    versionForm.value.artifact_key = response.data.artifact_key
    versionForm.value.artifact_sha256 = response.data.artifact_sha256
    versionForm.value.format = response.data.format
  } catch (error) {
    uploadError.value = describeModelOpsError(error, '上传失败，请稍后重试。')
  } finally {
    uploading.value = false
  }
}

const versionOpen = ref(false)
const versionForm = ref({
  artifact_key: '',
  artifact_sha256: '',
  format: 'jsonl' as 'jsonl' | 'csv',
  sample_count: 1,
  split_config: '',
  contains_sensitive_data: false,
})
const versionSubmitting = ref(false)
const versionError = ref('')
/** 每次打开登记版本表单生成一次幂等键；同一次提交的重试复用。 */
const versionKey = ref('')

function openVersionForm() {
  versionForm.value = {
    artifact_key: uploaded.value?.artifact_key ?? '',
    artifact_sha256: uploaded.value?.artifact_sha256 ?? '',
    format: uploaded.value?.format ?? 'jsonl',
    sample_count: 1,
    split_config: '',
    contains_sensitive_data: false,
  }
  versionError.value = ''
  versionKey.value = crypto.randomUUID()
  versionOpen.value = true
}

const canSubmitVersion = computed(
  () =>
    versionForm.value.artifact_key.trim().length >= 3 &&
    /^[0-9a-f]{64}$/.test(versionForm.value.artifact_sha256.trim()) &&
    Number.isInteger(versionForm.value.sample_count) &&
    versionForm.value.sample_count >= 1 &&
    !versionSubmitting.value,
)

async function submitVersion() {
  const dataset = detailDataset.value
  if (!dataset || !canSubmitVersion.value) {
    return
  }
  let splitConfig: Record<string, unknown> = {}
  const rawSplit = versionForm.value.split_config.trim()
  if (rawSplit) {
    try {
      const parsed: unknown = JSON.parse(rawSplit)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        versionError.value = 'split_config 必须是 JSON 对象。'
        return
      }
      splitConfig = parsed as Record<string, unknown>
    } catch {
      versionError.value = 'split_config 不是合法的 JSON。'
      return
    }
  }
  versionSubmitting.value = true
  versionError.value = ''
  try {
    await callApi(() =>
      createDatasetVersion({
        path: { dataset_id: dataset.id },
        body: {
          artifact_key: versionForm.value.artifact_key.trim(),
          artifact_sha256: versionForm.value.artifact_sha256.trim(),
          format: versionForm.value.format,
          sample_count: versionForm.value.sample_count,
          split_config: splitConfig,
          contains_sensitive_data: versionForm.value.contains_sensitive_data,
        },
        headers: { 'Idempotency-Key': versionKey.value },
      }),
    )
    versionOpen.value = false
    await refreshDetail(dataset.id)
    await load()
  } catch (error) {
    versionError.value = describeModelOpsError(error, '版本登记失败，请稍后重试。')
  } finally {
    versionSubmitting.value = false
  }
}

const freezeTarget = ref<DatasetVersion | null>(null)
const freezeSubmitting = ref(false)
const freezeError = ref('')
/** 每次打开冻结确认生成一次幂等键；同一次提交的重试复用。 */
const freezeKey = ref('')

function canFreeze(version: DatasetVersion): boolean {
  return version.validation_status === 'valid' && !version.contains_sensitive_data && !version.frozen_at
}

function openFreeze(version: DatasetVersion) {
  freezeTarget.value = version
  freezeError.value = ''
  freezeKey.value = crypto.randomUUID()
}

async function submitFreeze() {
  const dataset = detailDataset.value
  const target = freezeTarget.value
  if (!dataset || !target || freezeSubmitting.value) {
    return
  }
  freezeSubmitting.value = true
  freezeError.value = ''
  try {
    await callApi(() =>
      freezeDatasetVersion({
        path: { dataset_id: dataset.id, version: target.version },
        headers: { 'Idempotency-Key': freezeKey.value },
      }),
    )
    freezeTarget.value = null
    await refreshDetail(dataset.id)
  } catch (error) {
    freezeError.value = describeModelOpsError(error, '冻结失败，请稍后重试。')
  } finally {
    freezeSubmitting.value = false
  }
}

const deleteTarget = ref<Dataset | null>(null)
const deleteSubmitting = ref(false)
const deleteError = ref('')
/** 每次打开删除确认生成一次幂等键；同一次提交的重试复用。 */
const deleteKey = ref('')

function openDelete(dataset: Dataset) {
  deleteTarget.value = dataset
  deleteError.value = ''
  deleteKey.value = crypto.randomUUID()
}

async function submitDelete() {
  const target = deleteTarget.value
  if (!target || deleteSubmitting.value) {
    return
  }
  deleteSubmitting.value = true
  deleteError.value = ''
  try {
    await callApi(() =>
      deleteDataset({ path: { dataset_id: target.id }, headers: { 'Idempotency-Key': deleteKey.value } }),
    )
    deleteTarget.value = null
    if (detailDataset.value?.id === target.id) {
      detailOpen.value = false
    }
    await load()
  } catch (error) {
    deleteError.value = describeModelOpsError(error, '删除失败，请稍后重试。')
  } finally {
    deleteSubmitting.value = false
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function formatJson(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2)
}

function hasReportContent(report: Record<string, unknown> | undefined): boolean {
  return !!report && Object.keys(report).length > 0
}
</script>

<template>
  <div class="datasets">
    <PageHeader title="数据集" subtitle="训练与评估数据集：上传、校验、版本登记与冻结">
      <UiButton v-if="canWrite" variant="primary" @click="openCreate">创建数据集</UiButton>
    </PageHeader>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无数据集" description="创建数据集后可上传数据文件并登记版本" />
    <template v-else>
      <div class="datasets__list">
        <UiCard v-for="dataset in items" :key="dataset.id" class="datasets__item" padding="md" @click="openDetail(dataset)">
          <div class="datasets__item-head">
            <strong class="datasets__name">{{ dataset.name }}</strong>
            <span class="datasets__purpose">{{ PURPOSE_LABELS[dataset.purpose] }}</span>
            <time class="datasets__time">{{ formatTime(dataset.created_at) }}</time>
          </div>
          <p class="datasets__meta">
            最新版本：{{ dataset.latest_version ?? '暂无' }}
            <template v-if="dataset.description"> · {{ dataset.description }}</template>
          </p>
        </UiCard>
      </div>
      <div class="datasets__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <el-dialog v-model="createOpen" title="创建数据集" width="520px">
      <form class="datasets__form" @submit.prevent="submitCreate">
        <UiField label="名称" input-id="dataset-name" required hint="2–100 字">
          <input id="dataset-name" v-model="createForm.name" class="datasets__input" maxlength="100" :disabled="createSubmitting" />
        </UiField>
        <UiField label="用途" input-id="dataset-purpose" required>
          <select id="dataset-purpose" v-model="createForm.purpose" class="datasets__input" :disabled="createSubmitting">
            <option v-for="option in PURPOSE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </UiField>
        <UiField label="描述" input-id="dataset-description">
          <textarea id="dataset-description" v-model="createForm.description" class="datasets__input" rows="2" maxlength="500" :disabled="createSubmitting" />
        </UiField>
        <p v-if="createError" class="datasets__error" role="alert">{{ createError }}</p>
        <div class="datasets__dialog-actions">
          <UiButton variant="default" :disabled="createSubmitting" @click="createOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="createSubmitting" :disabled="!canSubmitCreate">创建</UiButton>
        </div>
      </form>
    </el-dialog>

    <el-dialog v-model="detailOpen" :title="detailDataset ? `数据集：${detailDataset.name}` : '数据集详情'" width="760px">
      <UiSkeleton v-if="detailLoading" :lines="6" />
      <ErrorState v-else-if="detailError" title="详情加载失败" :message="detailError" @retry="refreshDetail(detailId)" />
      <template v-else-if="detailDataset">
        <dl class="datasets__detail">
          <div><dt>用途</dt><dd>{{ PURPOSE_LABELS[detailDataset.purpose] }}</dd></div>
          <div><dt>最新版本</dt><dd>{{ detailDataset.latest_version ?? '暂无' }}</dd></div>
          <div><dt>创建时间</dt><dd>{{ formatTime(detailDataset.created_at) }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ formatTime(detailDataset.updated_at) }}</dd></div>
          <div v-if="detailDataset.description" class="datasets__detail-wide"><dt>描述</dt><dd>{{ detailDataset.description }}</dd></div>
        </dl>

        <div class="datasets__section-head">
          <h3 class="datasets__section-title">版本列表</h3>
          <div v-if="canWrite" class="datasets__section-actions">
            <UiButton variant="default" size="sm" @click="openVersionForm">登记版本</UiButton>
          </div>
        </div>

        <div v-if="canWrite" class="datasets__upload">
          <p class="datasets__upload-title">上传数据文件（JSONL / CSV，服务端计算 SHA-256）</p>
          <div class="datasets__upload-row">
            <input type="file" accept=".jsonl,.csv" :disabled="uploading" @change="onFileChange" />
            <UiButton variant="primary" size="sm" :loading="uploading" :disabled="!uploadFile" @click="submitUpload">上传</UiButton>
          </div>
          <p v-if="uploading && uploadFile" class="datasets__upload-status">正在上传 {{ uploadFile.name }}…</p>
          <p v-if="uploadError" class="datasets__error" role="alert">{{ uploadError }}</p>
          <p v-if="uploaded" class="datasets__upload-ok">
            上传完成：{{ uploaded.file_name }}（{{ uploaded.size_bytes }} 字节），SHA-256
            <span class="datasets__mono">{{ uploaded.artifact_sha256.slice(0, 16) }}…</span>，可在下方登记版本。
          </p>
        </div>

        <form v-if="canWrite && versionOpen" class="datasets__form datasets__version-form" @submit.prevent="submitVersion">
          <UiField label="artifact_key" input-id="version-key" required hint="来自上传结果，服务端生成的对象键">
            <input id="version-key" v-model="versionForm.artifact_key" class="datasets__input" :disabled="versionSubmitting" />
          </UiField>
          <UiField label="artifact_sha256" input-id="version-sha" required hint="64 位小写十六进制">
            <input id="version-sha" v-model="versionForm.artifact_sha256" class="datasets__input" maxlength="64" :disabled="versionSubmitting" />
          </UiField>
          <div class="datasets__form-row">
            <UiField label="格式" input-id="version-format" required>
              <select id="version-format" v-model="versionForm.format" class="datasets__input" :disabled="versionSubmitting">
                <option value="jsonl">JSONL</option>
                <option value="csv">CSV</option>
              </select>
            </UiField>
            <UiField label="样本数" input-id="version-samples" required>
              <input id="version-samples" v-model.number="versionForm.sample_count" class="datasets__input" type="number" min="1" :disabled="versionSubmitting" />
            </UiField>
          </div>
          <UiField label="split_config（可选，JSON 对象）" input-id="version-split">
            <textarea id="version-split" v-model="versionForm.split_config" class="datasets__input" rows="2" placeholder="{&quot;train&quot;: 0.9, &quot;test&quot;: 0.1}" :disabled="versionSubmitting" />
          </UiField>
          <label class="datasets__checkbox">
            <input v-model="versionForm.contains_sensitive_data" type="checkbox" :disabled="versionSubmitting" />
            声明包含敏感数据（含敏感数据的版本不能冻结或用于训练）
          </label>
          <p v-if="versionError" class="datasets__error" role="alert">{{ versionError }}</p>
          <div class="datasets__dialog-actions">
            <UiButton variant="default" :disabled="versionSubmitting" @click="versionOpen = false">取消</UiButton>
            <UiButton variant="primary" type="submit" :loading="versionSubmitting" :disabled="!canSubmitVersion">登记版本</UiButton>
          </div>
        </form>

        <EmptyState v-if="detailVersions.length === 0" title="暂无版本" description="上传数据文件后登记第一个版本" />
        <table v-else class="datasets__table">
          <thead>
            <tr>
              <th>版本</th>
              <th>格式</th>
              <th>样本数</th>
              <th>校验状态</th>
              <th>敏感数据</th>
              <th>冻结时间</th>
              <th v-if="canWrite">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="version in detailVersions" :key="version.version">
              <td>v{{ version.version }}</td>
              <td>{{ version.format.toUpperCase() }}</td>
              <td>{{ version.sample_count }}</td>
              <td>
                <StatusBadge :status="version.validation_status" />
                <details v-if="hasReportContent(version.validation_report)" class="datasets__report">
                  <summary>校验报告</summary>
                  <pre class="datasets__report-body">{{ formatJson(version.validation_report ?? {}) }}</pre>
                </details>
              </td>
              <td>{{ version.contains_sensitive_data ? '是' : '否' }}</td>
              <td>{{ version.frozen_at ? formatTime(version.frozen_at) : '未冻结' }}</td>
              <td v-if="canWrite">
                <UiButton v-if="canFreeze(version)" variant="text" size="sm" @click="openFreeze(version)">冻结</UiButton>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="canWrite" class="datasets__detail-actions">
          <UiButton variant="danger" @click="openDelete(detailDataset)">删除数据集</UiButton>
        </div>
      </template>
    </el-dialog>

    <el-dialog :model-value="freezeTarget !== null" title="冻结版本" width="480px" @update:model-value="freezeTarget = null">
      <template v-if="freezeTarget">
        <p class="datasets__confirm">确认冻结 v{{ freezeTarget.version }}？冻结后版本不可修改，可用于训练与评估。</p>
        <p v-if="freezeError" class="datasets__error" role="alert">{{ freezeError }}</p>
        <div class="datasets__dialog-actions">
          <UiButton variant="default" :disabled="freezeSubmitting" @click="freezeTarget = null">取消</UiButton>
          <UiButton variant="primary" :loading="freezeSubmitting" @click="submitFreeze">确认冻结</UiButton>
        </div>
      </template>
    </el-dialog>

    <el-dialog :model-value="deleteTarget !== null" title="删除数据集" width="480px" @update:model-value="deleteTarget = null">
      <template v-if="deleteTarget">
        <p class="datasets__confirm">
          确认删除 <span class="datasets__mono">{{ deleteTarget.name }}</span>？逻辑删除后不可恢复；被活动训练任务引用的数据集无法删除。
        </p>
        <p v-if="deleteError" class="datasets__error" role="alert">{{ deleteError }}</p>
        <div class="datasets__dialog-actions">
          <UiButton variant="default" :disabled="deleteSubmitting" @click="deleteTarget = null">取消</UiButton>
          <UiButton variant="danger" :loading="deleteSubmitting" @click="submitDelete">确认删除</UiButton>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.datasets {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.datasets__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.datasets__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.datasets__item:hover {
  border-color: var(--cp-muted);
}

.datasets__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.datasets__name {
  font-size: 14px;
  color: var(--cp-ink);
}

.datasets__purpose {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.datasets__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.datasets__meta {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.datasets__pagination {
  display: flex;
  justify-content: center;
}

.datasets__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.datasets__form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--cp-space-3);
}

.datasets__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  box-sizing: border-box;
}

textarea.datasets__input {
  resize: vertical;
}

.datasets__checkbox {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
}

.datasets__detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
  margin: 0 0 var(--cp-space-4);
}

.datasets__detail dt {
  font-size: 12px;
  color: var(--cp-muted);
}

.datasets__detail dd {
  margin: 0;
  font-size: 13px;
  color: var(--cp-ink);
}

.datasets__detail-wide {
  grid-column: 1 / -1;
}

.datasets__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--cp-space-2);
}

.datasets__section-title {
  margin: 0;
  font-size: 14px;
  color: var(--cp-ink);
}

.datasets__upload {
  border: 1px dashed var(--cp-hairline-strong);
  border-radius: var(--cp-radius-card);
  padding: var(--cp-space-3);
  margin-bottom: var(--cp-space-3);
}

.datasets__upload-title {
  margin: 0 0 var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
}

.datasets__upload-row {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.datasets__upload-status {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.datasets__upload-ok {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-success);
}

.datasets__version-form {
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  padding: var(--cp-space-3);
  margin-bottom: var(--cp-space-3);
}

.datasets__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.datasets__table th {
  text-align: left;
  padding: var(--cp-space-2);
  color: var(--cp-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--cp-hairline);
  white-space: nowrap;
}

.datasets__table td {
  padding: var(--cp-space-2);
  border-bottom: 1px solid var(--cp-hairline-soft);
  vertical-align: top;
}

.datasets__table tr:last-child td {
  border-bottom: none;
}

.datasets__report summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--cp-primary);
}

.datasets__report-body {
  margin: var(--cp-space-1) 0 0;
  padding: var(--cp-space-2);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas-soft);
  font-family: var(--cp-font-mono);
  font-size: 12px;
  max-height: 160px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.datasets__mono {
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.datasets__confirm {
  margin: 0;
  font-size: 14px;
  color: var(--cp-body);
}

.datasets__error {
  margin: var(--cp-space-2) 0 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.datasets__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
}

.datasets__detail-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--cp-space-4);
}
</style>
