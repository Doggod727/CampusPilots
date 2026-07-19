<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import {
  deactivateDocument,
  deleteDocument,
  getIngestionJob,
  listDocumentChunks,
  listDocuments,
  listKnowledgeBases,
  publishDocument,
  retryIngestionJob,
  uploadDocuments,
} from '@/api/generated'
import type { Document, DocumentChunk, DocumentStatus, IngestionJob, IngestionStage, KnowledgeBase } from '@/api/generated'
import { useResourceList } from '@/shared/lib/useResourceList'
import { EmptyState, ErrorState, PageHeader, StatusBadge, UiButton, UiCard, UiField, UiPagination, UiSkeleton } from '@/shared/ui'

const route = useRoute()

const MAX_FILES = 10
const MAX_FILE_BYTES = 20 * 1024 * 1024
const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md']
const ACTIVE_STAGES: ReadonlySet<IngestionStage> = new Set(['queued', 'parsing', 'cleaning', 'splitting', 'embedding', 'indexing'])
const POLL_INTERVAL_MS = 4000

const DOC_STATUS_FILTERS: Array<{ value: DocumentStatus | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待入库' },
  { value: 'processing', label: '入库中' },
  { value: 'ready', label: '就绪' },
  { value: 'published', label: '已发布' },
  { value: 'inactive', label: '已停用' },
  { value: 'failed', label: '失败' },
]
const DOC_STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: '待入库',
  processing: '入库中',
  ready: '就绪',
  published: '已发布',
  inactive: '已停用',
  failed: '失败',
  deleted: '已删除',
}
const STAGE_LABELS: Record<IngestionStage, string> = {
  queued: '排队中',
  parsing: '解析中',
  cleaning: '清洗中',
  splitting: '切分中',
  embedding: '向量化中',
  indexing: '索引中',
  succeeded: '已完成',
  failed: '失败',
}
const CLEAN_LABELS: Record<DocumentChunk['clean_status'], string> = { clean: '正常', redacted: '已脱敏', excluded: '已排除' }

/* ---------- 知识库选择 ---------- */

const kbs = ref<KnowledgeBase[]>([])
const kbLoading = ref(true)
const kbFailed = ref(false)
const selectedKbId = ref('')

async function loadKbs() {
  kbLoading.value = true
  kbFailed.value = false
  try {
    const response = await callApi(() => listKnowledgeBases({ query: { page: 1, page_size: 50 } }))
    kbs.value = response.data.items
    const fromQuery = typeof route.query.kb === 'string' ? route.query.kb : ''
    if (fromQuery && kbs.value.some((kb) => kb.id === fromQuery)) {
      selectedKbId.value = fromQuery
    } else if (!selectedKbId.value || !kbs.value.some((kb) => kb.id === selectedKbId.value)) {
      selectedKbId.value = kbs.value[0]?.id ?? ''
    }
  } catch {
    kbFailed.value = true
  } finally {
    kbLoading.value = false
  }
}

const selectedKb = computed(() => kbs.value.find((kb) => kb.id === selectedKbId.value) ?? null)

/* ---------- 文档列表 ---------- */

const searchInput = ref('')
const searchQuery = ref('')
const statusFilter = ref<DocumentStatus | ''>('')

const list = useResourceList<Document>(async (page, pageSize) => {
  if (!selectedKbId.value) {
    // 知识库尚未加载完成：不发请求，等待 watch(selectedKbId) 触发首次加载
    return { items: [], total: 0 }
  }
  const response = await callApi(() =>
    listDocuments({
      path: { knowledge_base_id: selectedKbId.value },
      query: {
        page,
        page_size: pageSize,
        ...(searchQuery.value ? { q: searchQuery.value } : {}),
        ...(statusFilter.value ? { status: statusFilter.value as DocumentStatus } : {}),
      },
    }),
  )
  return { items: response.data.items, total: response.data.pagination.total }
}, 10)

watch(selectedKbId, async (next, prev) => {
  if (!next || next === prev) {
    return
  }
  closeChunks()
  pendingAction.value = null
  confirmingDeleteId.value = null
  list.page.value = 1
  await list.load()
})

async function applySearch() {
  searchQuery.value = searchInput.value.trim()
  list.page.value = 1
  await list.load()
}

async function changeStatus(value: DocumentStatus | '') {
  statusFilter.value = value
  list.page.value = 1
  await list.load()
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KiB`
  }
  return `${bytes} B`
}

function describeActionError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '权限不足：需要知识库写权限（knowledge:write / knowledge:publish）。'
    }
    if (error.status === 404) {
      return '资源不存在或已被删除。'
    }
    if (error.status === 409) {
      switch (error.code) {
        case 'DOCUMENT_ALREADY_EXISTS':
          return '文档已存在：相同内容的文件已上传过。'
        case 'DOCUMENT_NOT_READY':
          return '文档尚未入库就绪，不能发布。'
        case 'DOCUMENT_INDEX_INCOMPLETE':
          return '文档索引尚未完整，请等待入库完成后重试。'
        case 'INGESTION_RETRY_NOT_ALLOWED':
          return '该任务不可重试：仅失败且未达最大尝试次数的任务可重试。'
        case 'RESOURCE_VERSION_CONFLICT':
          return '数据已被他人修改（版本冲突），请刷新后重试。'
        default:
          return '状态冲突，请刷新列表后重试。'
      }
    }
    if (error.status === 413) {
      return '超出上传限制：单文件不超过 20 MiB，每批最多 10 个文件。'
    }
    if (error.status === 415) {
      return '文件格式不支持：仅允许 PDF、DOCX、TXT、MD。'
    }
    if (error.status === 422) {
      return error.details[0]?.reason ?? '输入校验失败，请检查后重试。'
    }
    if (error.status === 429) {
      return '请求过于频繁，请稍后再试。'
    }
  }
  return fallback
}

/* ---------- 上传 ---------- */

const showUpload = ref(false)
const uploadFiles = ref<File[]>([])
const uploadError = ref('')
const uploading = ref(false)
const uploadNotice = ref('')
/** 同一上传批次固定幂等键：失败重试复用，成功后更换。 */
const uploadKey = ref(crypto.randomUUID())
const fileInput = ref<HTMLInputElement | null>(null)

function pickFiles(event: Event) {
  const input = event.target as HTMLInputElement
  uploadFiles.value = Array.from(input.files ?? [])
  uploadError.value = ''
}

const uploadValidationError = computed(() => {
  if (uploadFiles.value.length === 0) {
    return ''
  }
  if (uploadFiles.value.length > MAX_FILES) {
    return `每批最多上传 ${MAX_FILES} 个文件，当前已选 ${uploadFiles.value.length} 个。`
  }
  for (const file of uploadFiles.value) {
    if (file.size > MAX_FILE_BYTES) {
      return `文件「${file.name}」超过 20 MiB 上限。`
    }
    const lower = file.name.toLowerCase()
    if (!ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
      return `文件「${file.name}」类型不支持：仅允许 PDF、DOCX、TXT、MD。`
    }
  }
  return ''
})

async function submitUpload() {
  if (uploadFiles.value.length === 0 || uploadValidationError.value || uploading.value) {
    return
  }
  uploading.value = true
  uploadError.value = ''
  uploadNotice.value = ''
  try {
    const response = await callApi(() =>
      uploadDocuments({
        path: { knowledge_base_id: selectedKbId.value },
        body: { files: uploadFiles.value },
        headers: { 'Idempotency-Key': uploadKey.value },
      }),
    )
    uploadKey.value = crypto.randomUUID()
    uploadNotice.value = `已接收 ${response.data.items.length} 个文档，入库任务已排队。`
    uploadFiles.value = []
    if (fileInput.value) {
      fileInput.value.value = ''
    }
    showUpload.value = false
    await list.load()
  } catch (error) {
    uploadError.value = describeActionError(error, '上传失败，请稍后重试。')
  } finally {
    uploading.value = false
  }
}

/* ---------- 入库任务与轮询（状态完全以后端为准，刷新页面后从 latest_job 恢复） ---------- */

const jobs = computed(() =>
  list.items.value
    .filter((doc) => doc.latest_job)
    .map((doc) => ({ doc, job: doc.latest_job as IngestionJob })),
)

const hasActiveJobs = computed(() => jobs.value.some(({ job }) => ACTIVE_STAGES.has(job.stage)))

let pollTimer: ReturnType<typeof setInterval> | null = null
let polling = false

async function pollOnce() {
  if (polling) {
    return
  }
  polling = true
  try {
    const active = jobs.value.filter(({ job }) => ACTIVE_STAGES.has(job.stage))
    let reachedTerminal = false
    for (const { doc, job } of active) {
      try {
        const response = await callApi(() => getIngestionJob({ path: { job_id: job.id } }))
        doc.latest_job = response.data
        if (!ACTIVE_STAGES.has(response.data.stage)) {
          reachedTerminal = true
        }
      } catch {
        /* 单次轮询失败下一轮再试 */
      }
    }
    if (reachedTerminal) {
      // 任务到达终态：刷新文档列表以同步 status/chunk_count 等派生字段
      await list.load()
    }
  } finally {
    polling = false
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(
  hasActiveJobs,
  (active) => {
    if (active && pollTimer === null) {
      pollTimer = setInterval(() => {
        void pollOnce()
      }, POLL_INTERVAL_MS)
    } else if (!active) {
      stopPolling()
    }
  },
  { immediate: true },
)

onUnmounted(stopPolling)

const retryingJobId = ref('')
const retryError = ref('')

async function retryJob(job: IngestionJob) {
  if (retryingJobId.value) {
    return
  }
  retryingJobId.value = job.id
  retryError.value = ''
  try {
    await callApi(() =>
      retryIngestionJob({
        path: { job_id: job.id },
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      }),
    )
    await list.load()
  } catch (error) {
    retryError.value = describeActionError(error, '重试失败，请稍后再试。')
  } finally {
    retryingJobId.value = ''
  }
}

/* ---------- 发布 / 停用 / 删除 ---------- */

const pendingAction = ref<{ type: 'publish' | 'deactivate'; doc: Document } | null>(null)
const actionForm = reactive({ reason: '' })
const actionError = ref('')
const actionBusy = ref(false)
/** 同一操作会话固定幂等键：失败重试复用，成功或取消后更换。 */
const actionKey = ref(crypto.randomUUID())

function openAction(type: 'publish' | 'deactivate', doc: Document) {
  pendingAction.value = { type, doc }
  actionForm.reason = ''
  actionError.value = ''
  actionKey.value = crypto.randomUUID()
}

async function submitAction() {
  const action = pendingAction.value
  if (!action || !actionForm.reason.trim() || actionBusy.value) {
    return
  }
  actionBusy.value = true
  actionError.value = ''
  const request = action.type === 'publish' ? publishDocument : deactivateDocument
  try {
    await callApi(() =>
      request({
        path: { document_id: action.doc.id },
        body: { reason: actionForm.reason.trim(), version: action.doc.version },
        headers: { 'Idempotency-Key': actionKey.value },
      }),
    )
    pendingAction.value = null
    await list.load()
  } catch (error) {
    actionError.value = describeActionError(error, '操作失败，请稍后重试。')
  } finally {
    actionBusy.value = false
  }
}

const confirmingDeleteId = ref<string | null>(null)
const deleting = ref(false)
const deleteError = ref('')

async function confirmDelete(doc: Document) {
  if (deleting.value) {
    return
  }
  deleting.value = true
  deleteError.value = ''
  try {
    await callApi(() => deleteDocument({ path: { document_id: doc.id } }))
    confirmingDeleteId.value = null
    if (chunksDoc.value?.id === doc.id) {
      closeChunks()
    }
    await list.load()
  } catch (error) {
    deleteError.value = describeActionError(error, '删除失败，请稍后重试。')
  } finally {
    deleting.value = false
  }
}

/* ---------- Chunk 查看 ---------- */

const chunksDoc = ref<Document | null>(null)
const chunks = ref<DocumentChunk[]>([])
const chunksTotal = ref(0)
const chunksPage = ref(1)
const chunksLoading = ref(false)
const chunksError = ref('')
const CHUNKS_PAGE_SIZE = 10

async function loadChunks() {
  if (!chunksDoc.value) {
    return
  }
  chunksLoading.value = true
  chunksError.value = ''
  try {
    const response = await callApi(() =>
      listDocumentChunks({
        path: { document_id: (chunksDoc.value as Document).id },
        query: { page: chunksPage.value, page_size: CHUNKS_PAGE_SIZE },
      }),
    )
    chunks.value = response.data.items
    chunksTotal.value = response.data.pagination.total
  } catch (error) {
    chunksError.value = describeActionError(error, 'Chunk 加载失败，请稍后重试。')
  } finally {
    chunksLoading.value = false
  }
}

async function openChunks(doc: Document) {
  chunksDoc.value = doc
  chunksPage.value = 1
  await loadChunks()
}

async function changeChunksPage(next: number) {
  chunksPage.value = next
  await loadChunks()
}

function closeChunks() {
  chunksDoc.value = null
  chunks.value = []
}

onMounted(loadKbs)
</script>

<template>
  <div class="ingest">
    <PageHeader title="文档与入库" subtitle="选择知识库后管理文档：上传、入库任务、Chunk、发布与停用">
      <UiButton variant="primary" :disabled="!selectedKbId" @click="showUpload = !showUpload">
        {{ showUpload ? '收起' : '上传文档' }}
      </UiButton>
    </PageHeader>

    <UiSkeleton v-if="kbLoading" :lines="2" />
    <ErrorState v-else-if="kbFailed" title="知识库列表加载失败" @retry="loadKbs" />
    <EmptyState v-else-if="kbs.length === 0" title="暂无可管理知识库" description="请先在“知识库管理”页创建知识库" />
    <template v-else>
      <div class="ingest__kb-bar">
        <label class="ingest__kb-label" for="kb-select">知识库</label>
        <select id="kb-select" v-model="selectedKbId" class="ingest__select">
          <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}（文档 {{ kb.document_count }}）</option>
        </select>
        <span v-if="selectedKb" class="ingest__kb-meta">{{ selectedKb.embedding_model }} · 切分 {{ selectedKb.chunk_size }}/{{ selectedKb.chunk_overlap }}</span>
      </div>

      <UiCard v-if="showUpload" class="ingest__panel" padding="md">
        <h2 class="ingest__panel-title">批量上传（每批 ≤10 个，单文件 ≤20 MiB，PDF/DOCX/TXT/MD）</h2>
        <form class="ingest__form" @submit.prevent="submitUpload">
          <UiField label="选择文件" input-id="doc-files" required>
            <input id="doc-files" ref="fileInput" type="file" multiple accept=".pdf,.docx,.txt,.md" :disabled="uploading" @change="pickFiles" />
          </UiField>
          <ul v-if="uploadFiles.length > 0" class="ingest__files">
            <li v-for="file in uploadFiles" :key="file.name + file.size">{{ file.name }}（{{ formatSize(file.size) }}）</li>
          </ul>
          <p v-if="uploadValidationError" class="ingest__error" role="alert">{{ uploadValidationError }}</p>
          <p v-else-if="uploadError" class="ingest__error" role="alert">{{ uploadError }}</p>
          <div class="ingest__form-actions">
            <UiButton variant="primary" type="submit" :loading="uploading" :disabled="uploadFiles.length === 0 || !!uploadValidationError">
              上传并入库
            </UiButton>
          </div>
        </form>
      </UiCard>
      <p v-if="uploadNotice" class="ingest__notice" role="status">{{ uploadNotice }}</p>

      <div class="ingest__toolbar">
        <form class="ingest__search" @submit.prevent="applySearch">
          <input v-model="searchInput" class="ingest__input" type="search" maxlength="100" placeholder="按标题或文件名搜索" aria-label="搜索文档" />
          <UiButton type="submit">搜索</UiButton>
        </form>
        <div class="ingest__filters" role="tablist" aria-label="状态筛选">
          <button
            v-for="filter in DOC_STATUS_FILTERS"
            :key="filter.label"
            type="button"
            class="ingest__filter"
            :class="{ 'ingest__filter--active': statusFilter === filter.value }"
            @click="changeStatus(filter.value)"
          >
            {{ filter.label }}
          </button>
        </div>
      </div>

      <section v-if="jobs.length > 0" class="ingest__jobs" aria-label="入库任务">
        <h2 class="ingest__section-title">入库任务{{ hasActiveJobs ? '（进行中，每 4 秒自动刷新）' : '' }}</h2>
        <p v-if="retryError" class="ingest__error" role="alert">{{ retryError }}</p>
        <div class="ingest__job-list">
          <UiCard v-for="{ doc, job } in jobs" :key="job.id" padding="sm" class="ingest__job">
            <div class="ingest__job-head">
              <span class="ingest__job-title">{{ doc.title }}</span>
              <StatusBadge :status="job.stage" :label="STAGE_LABELS[job.stage]" />
              <span class="ingest__job-progress">{{ job.progress }}%</span>
            </div>
            <div class="ingest__job-bar" role="progressbar" :aria-valuenow="job.progress" aria-valuemin="0" aria-valuemax="100">
              <div class="ingest__job-bar-fill" :style="{ width: `${job.progress}%` }" />
            </div>
            <p class="ingest__job-meta">第 {{ job.attempt }}/{{ job.max_attempts }} 次尝试 · 更新于 {{ formatTime(job.updated_at) }}</p>
            <p v-if="job.stage === 'failed' && job.error_message" class="ingest__job-error">{{ job.error_code }}：{{ job.error_message }}</p>
            <div v-if="job.stage === 'failed'" class="ingest__job-actions">
              <UiButton size="sm" :loading="retryingJobId === job.id" @click="retryJob(job)">重试入库</UiButton>
            </div>
          </UiCard>
        </div>
      </section>

      <p v-if="deleteError" class="ingest__error" role="alert">{{ deleteError }}</p>

      <UiSkeleton v-if="list.loading.value" :lines="5" />
      <ErrorState v-else-if="list.failed.value" title="文档列表加载失败" @retry="list.load" />
      <EmptyState v-else-if="list.isEmpty.value" title="暂无文档" description="点击右上角“上传文档”添加第一批资料" />
      <template v-else>
        <div class="ingest__list">
          <UiCard v-for="doc in list.items.value" :key="doc.id" class="ingest__item" padding="md">
            <div class="ingest__item-head">
              <h3 class="ingest__item-name">{{ doc.title }}</h3>
              <StatusBadge :status="doc.status" :label="DOC_STATUS_LABELS[doc.status]" />
            </div>
            <p class="ingest__meta">
              {{ doc.original_file_name }} · {{ formatSize(doc.file_size_bytes) }} · Chunk {{ doc.chunk_count
              }}<template v-if="doc.page_count != null"> · {{ doc.page_count }} 页</template>
              · 上传于 {{ formatTime(doc.created_at) }}
            </p>
            <div class="ingest__item-actions">
              <UiButton size="sm" :disabled="doc.chunk_count === 0" @click="openChunks(doc)">查看 Chunk</UiButton>
              <UiButton v-if="doc.status === 'ready'" size="sm" variant="primary" @click="openAction('publish', doc)">发布</UiButton>
              <UiButton v-if="doc.status === 'published'" size="sm" @click="openAction('deactivate', doc)">停用</UiButton>
              <template v-if="confirmingDeleteId === doc.id">
                <span class="ingest__confirm-text">确认删除？</span>
                <UiButton size="sm" variant="danger" :loading="deleting" @click="confirmDelete(doc)">确认删除</UiButton>
                <UiButton size="sm" @click="confirmingDeleteId = null">取消</UiButton>
              </template>
              <UiButton v-else size="sm" variant="danger" @click="confirmingDeleteId = doc.id; deleteError = ''">删除</UiButton>
            </div>
          </UiCard>
        </div>
        <div class="ingest__pagination">
          <UiPagination :page="list.page.value" :total="list.total.value" :page-size="list.pageSize" @change="list.changePage" />
        </div>
      </template>

      <UiCard v-if="pendingAction" class="ingest__panel" padding="md">
        <h2 class="ingest__panel-title">
          {{ pendingAction.type === 'publish' ? `发布文档：${pendingAction.doc.title}` : `停用文档：${pendingAction.doc.title}` }}
        </h2>
        <form class="ingest__form" @submit.prevent="submitAction">
          <UiField label="操作原因" input-id="doc-action-reason" required hint="将写入审计日志">
            <textarea id="doc-action-reason" v-model="actionForm.reason" class="ingest__input" rows="2" maxlength="200" :disabled="actionBusy" />
          </UiField>
          <p v-if="actionError" class="ingest__error" role="alert">{{ actionError }}</p>
          <div class="ingest__form-actions">
            <UiButton variant="primary" type="submit" :loading="actionBusy" :disabled="!actionForm.reason.trim()">
              {{ pendingAction.type === 'publish' ? '确认发布' : '确认停用' }}
            </UiButton>
            <UiButton @click="pendingAction = null">取消</UiButton>
          </div>
        </form>
      </UiCard>

      <UiCard v-if="chunksDoc" class="ingest__panel" padding="md">
        <div class="ingest__chunks-head">
          <h2 class="ingest__panel-title">Chunk 预览：{{ chunksDoc.title }}</h2>
          <UiButton size="sm" @click="closeChunks">关闭</UiButton>
        </div>
        <UiSkeleton v-if="chunksLoading" :lines="4" />
        <p v-else-if="chunksError" class="ingest__error" role="alert">{{ chunksError }}</p>
        <EmptyState v-else-if="chunks.length === 0" title="暂无 Chunk" description="文档尚未完成切分" />
        <template v-else>
          <div class="ingest__chunks">
            <article v-for="chunk in chunks" :key="chunk.id" class="ingest__chunk">
              <p class="ingest__chunk-meta">
                #{{ chunk.chunk_index }} · {{ chunk.source_location
                }}<template v-if="chunk.page_number != null"> · 第 {{ chunk.page_number }} 页</template>
                · {{ chunk.token_count }} tokens · {{ CLEAN_LABELS[chunk.clean_status] }}
              </p>
              <p class="ingest__chunk-content">{{ chunk.content }}</p>
            </article>
          </div>
          <div class="ingest__pagination">
            <UiPagination :page="chunksPage" :total="chunksTotal" :page-size="CHUNKS_PAGE_SIZE" @change="changeChunksPage" />
          </div>
        </template>
      </UiCard>
    </template>
  </div>
</template>

<style scoped>
.ingest {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.ingest__kb-bar {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.ingest__kb-label {
  font-size: 13px;
  color: var(--cp-muted);
}

.ingest__select,
.ingest__input {
  min-height: var(--cp-control-md);
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  color: var(--cp-ink);
  font-family: var(--cp-font-sans);
  font-size: 14px;
}

textarea.ingest__input {
  padding: var(--cp-space-2) var(--cp-space-3);
  resize: vertical;
  width: 100%;
}

.ingest__kb-meta {
  font-size: 12px;
  color: var(--cp-muted-soft);
  font-family: var(--cp-font-mono);
}

.ingest__toolbar {
  display: flex;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
}

.ingest__search {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.ingest__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.ingest__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.ingest__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.ingest__panel {
  max-width: 860px;
}

.ingest__panel-title {
  margin: 0 0 var(--cp-space-3);
  font-size: 15px;
  color: var(--cp-ink);
}

.ingest__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.ingest__files {
  margin: 0;
  padding-left: var(--cp-space-5);
  font-size: 13px;
  color: var(--cp-body);
}

.ingest__form-actions {
  display: flex;
  gap: var(--cp-space-2);
}

.ingest__error {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.ingest__notice {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
  color: var(--cp-success);
  font-size: 13px;
}

.ingest__section-title {
  margin: 0 0 var(--cp-space-2);
  font-size: 14px;
  color: var(--cp-muted);
}

.ingest__job-list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.ingest__job-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.ingest__job-title {
  font-size: 14px;
  color: var(--cp-ink);
}

.ingest__job-progress {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.ingest__job-bar {
  margin-top: var(--cp-space-2);
  height: 6px;
  border-radius: var(--cp-radius-pill);
  background: var(--cp-hairline-soft);
  overflow: hidden;
}

.ingest__job-bar-fill {
  height: 100%;
  background: var(--cp-info);
  transition: width 0.3s ease;
}

.ingest__job-meta {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.ingest__job-error {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-error);
}

.ingest__job-actions {
  margin-top: var(--cp-space-2);
}

.ingest__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.ingest__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.ingest__item-name {
  margin: 0;
  font-size: 16px;
  color: var(--cp-ink);
}

.ingest__meta {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.ingest__item-actions {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-3);
  flex-wrap: wrap;
}

.ingest__confirm-text {
  font-size: 13px;
  color: var(--cp-error);
}

.ingest__chunks-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-2);
}

.ingest__chunks {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.ingest__chunk {
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas-soft);
}

.ingest__chunk-meta {
  margin: 0 0 var(--cp-space-1);
  font-size: 12px;
  color: var(--cp-muted);
}

.ingest__chunk-content {
  margin: 0;
  font-size: 13px;
  color: var(--cp-body);
  white-space: pre-wrap;
  word-break: break-word;
}

.ingest__pagination {
  display: flex;
  justify-content: center;
  margin-top: var(--cp-space-3);
}
</style>
