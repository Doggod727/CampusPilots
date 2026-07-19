<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import { createTopic, deleteTopic, listTopics, updateTopic } from '@/api/generated'
import type { Topic, TopicStatus } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { EmptyState, ErrorState, PageHeader, StatusBadge, UiButton, UiCard, UiField, UiPagination, UiSkeleton } from '@/shared/ui'

const router = useRouter()
const auth = useAuthStore()

const items = ref<Topic[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const loading = ref(true)
const failed = ref(false)

const STATUS_FILTERS: Array<{ value: TopicStatus | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'active', label: '启用中' },
  { value: 'archived', label: '已归档' },
]
const statusFilter = ref<TopicStatus | ''>('')

/** 话题管理操作按 openapi x-permissions 门控（community:moderate）。 */
const canModerate = computed(() => auth.hasPermission('community:moderate'))

async function load() {
  loading.value = true
  failed.value = false
  try {
    const response = await callApi(() =>
      listTopics({
        query: {
          page: page.value,
          page_size: pageSize,
          ...(statusFilter.value ? { status: statusFilter.value } : {}),
        },
      }),
    )
    items.value = response.data.items
    total.value = response.data.pagination.total
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function changePage(next: number) {
  page.value = next
  await load()
}

async function changeFilter(value: TopicStatus | '') {
  statusFilter.value = value
  page.value = 1
  await load()
}

function openPosts(topic: Topic) {
  void router.push({ name: 'community-posts', query: { topicId: topic.id } })
}

/* ---------- 创建 ---------- */

const createOpen = ref(false)
const createSubmitting = ref(false)
const createFailure = ref('')
/** 对话框一次会话固定幂等键：重试复用，避免重复创建。 */
const createKey = ref('')
const createForm = reactive({ code: '', name: '', description: '', allow_anonymous: false, sort_order: 0 })

const canSubmitCreate = computed(
  () => createForm.code.trim().length > 0 && createForm.name.trim().length > 0 && !createSubmitting.value,
)

function openCreate() {
  createForm.code = ''
  createForm.name = ''
  createForm.description = ''
  createForm.allow_anonymous = false
  createForm.sort_order = 0
  createFailure.value = ''
  createKey.value = crypto.randomUUID()
  createOpen.value = true
}

async function submitCreate() {
  if (!canSubmitCreate.value) {
    return
  }
  createSubmitting.value = true
  createFailure.value = ''
  try {
    await callApi(() =>
      createTopic({
        body: {
          code: createForm.code.trim(),
          name: createForm.name.trim(),
          description: createForm.description.trim() || null,
          allow_anonymous: createForm.allow_anonymous,
          sort_order: createForm.sort_order,
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createOpen.value = false
    await load()
  } catch (error) {
    createFailure.value = describeError(error, 'create')
  } finally {
    createSubmitting.value = false
  }
}

/* ---------- 编辑（乐观锁） ---------- */

const editOpen = ref(false)
const editSubmitting = ref(false)
const editFailure = ref('')
const editingTopic = ref<Topic | null>(null)
const editForm = reactive({
  name: '',
  description: '',
  allow_anonymous: false,
  sort_order: 0,
  status: 'active' as TopicStatus,
})

const canSubmitEdit = computed(() => editForm.name.trim().length > 0 && !editSubmitting.value)

function openEdit(topic: Topic) {
  editingTopic.value = topic
  editForm.name = topic.name
  editForm.description = topic.description ?? ''
  editForm.allow_anonymous = topic.allow_anonymous
  editForm.sort_order = topic.sort_order
  editForm.status = topic.status
  editFailure.value = ''
  editOpen.value = true
}

async function submitEdit() {
  const topic = editingTopic.value
  if (!topic || !canSubmitEdit.value) {
    return
  }
  editSubmitting.value = true
  editFailure.value = ''
  try {
    await callApi(() =>
      updateTopic({
        path: { topic_id: topic.id },
        body: {
          name: editForm.name.trim(),
          description: editForm.description.trim() || null,
          allow_anonymous: editForm.allow_anonymous,
          sort_order: editForm.sort_order,
          status: editForm.status,
          version: topic.version,
        },
      }),
    )
    editOpen.value = false
    await load()
  } catch (error) {
    editFailure.value = describeError(error, 'update')
  } finally {
    editSubmitting.value = false
  }
}

/* ---------- 删除（逻辑删除；有帖子时后端拒绝） ---------- */

const deleteFailure = ref('')
const deletingId = ref<string | null>(null)

async function removeTopic(topic: Topic) {
  if (deletingId.value !== null) {
    return
  }
  deletingId.value = topic.id
  deleteFailure.value = ''
  try {
    await callApi(() => deleteTopic({ path: { topic_id: topic.id } }))
    await load()
  } catch (error) {
    deleteFailure.value = describeError(error, 'delete')
  } finally {
    deletingId.value = null
  }
}

function describeError(error: unknown, operation: 'create' | 'update' | 'delete'): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '当前账号没有话题管理权限'
    }
    if (error.status === 404) {
      return '话题不存在或已被删除，请刷新列表'
    }
    if (error.status === 409) {
      if (operation === 'create') {
        return '话题编码已存在，请更换后重试'
      }
      if (operation === 'update') {
        return '版本冲突：该话题刚被他人修改，请刷新列表后重试'
      }
      return '该话题下仍存在帖子，不能删除'
    }
    if (error.status === 422) {
      return error.details[0]?.reason ?? '输入内容不符合要求'
    }
    if (error.status === 429) {
      return '操作过于频繁，请稍后再试'
    }
  }
  return '服务暂不可用，请稍后重试'
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<template>
  <div class="topics">
    <PageHeader title="社区话题" subtitle="浏览全部话题；点击话题查看其中的帖子">
      <UiButton v-if="canModerate" variant="primary" @click="openCreate">创建话题</UiButton>
    </PageHeader>

    <div class="topics__filters" role="tablist">
      <button
        v-for="filter in STATUS_FILTERS"
        :key="filter.label"
        type="button"
        class="topics__filter"
        :class="{ 'topics__filter--active': statusFilter === filter.value }"
        @click="changeFilter(filter.value)"
      >
        {{ filter.label }}
      </button>
    </div>

    <p v-if="deleteFailure" class="topics__alert" role="alert">{{ deleteFailure }}</p>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="话题列表加载失败" @retry="load" />
    <EmptyState v-else-if="items.length === 0" title="暂无话题" description="当前筛选条件下没有话题" />
    <template v-else>
      <div class="topics__list">
        <UiCard v-for="topic in items" :key="topic.id" class="topics__item" padding="md" @click="openPosts(topic)">
          <div class="topics__item-head">
            <strong class="topics__name">{{ topic.name }}</strong>
            <code class="topics__code">{{ topic.code }}</code>
            <StatusBadge :status="topic.status" :label="topic.status === 'active' ? '启用中' : '已归档'" />
            <span v-if="topic.allow_anonymous" class="topics__tag">允许匿名</span>
            <time class="topics__time">更新于 {{ formatTime(topic.updated_at) }}</time>
          </div>
          <p v-if="topic.description" class="topics__desc">{{ topic.description }}</p>
          <div v-if="canModerate" class="topics__actions" @click.stop>
            <UiButton size="sm" @click="openEdit(topic)">编辑</UiButton>
            <el-popconfirm
              title="确认删除该话题？仅没有帖子的话题可以删除"
              confirm-button-text="删除"
              cancel-button-text="取消"
              width="240"
              @confirm="removeTopic(topic)"
            >
              <template #reference>
                <UiButton size="sm" variant="danger" :loading="deletingId === topic.id">删除</UiButton>
              </template>
            </el-popconfirm>
          </div>
        </UiCard>
      </div>
      <div class="topics__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <el-dialog v-model="createOpen" title="创建话题" width="480px">
      <form class="topics__form" @submit.prevent="submitCreate">
        <UiField label="话题编码" input-id="topic-code" required hint="唯一标识，创建后不可修改">
          <input id="topic-code" v-model="createForm.code" class="topics__input" maxlength="50" :disabled="createSubmitting" />
        </UiField>
        <UiField label="话题名称" input-id="topic-name" required>
          <input id="topic-name" v-model="createForm.name" class="topics__input" maxlength="50" :disabled="createSubmitting" />
        </UiField>
        <UiField label="话题描述" input-id="topic-desc">
          <textarea id="topic-desc" v-model="createForm.description" class="topics__input" rows="3" maxlength="500" :disabled="createSubmitting" />
        </UiField>
        <UiField label="展示顺序" input-id="topic-sort" hint="数字越小越靠前">
          <input id="topic-sort" v-model.number="createForm.sort_order" class="topics__input" type="number" :disabled="createSubmitting" />
        </UiField>
        <label class="topics__check">
          <input v-model="createForm.allow_anonymous" type="checkbox" :disabled="createSubmitting" />
          允许在该话题下匿名发帖
        </label>
        <p v-if="createFailure" class="topics__form-error" role="alert">{{ createFailure }}</p>
        <div class="topics__form-actions">
          <UiButton @click="createOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="createSubmitting" :disabled="!canSubmitCreate">创建</UiButton>
        </div>
      </form>
    </el-dialog>

    <el-dialog v-model="editOpen" title="编辑话题" width="480px">
      <form class="topics__form" @submit.prevent="submitEdit">
        <UiField label="话题名称" input-id="topic-edit-name" required>
          <input id="topic-edit-name" v-model="editForm.name" class="topics__input" maxlength="50" :disabled="editSubmitting" />
        </UiField>
        <UiField label="话题描述" input-id="topic-edit-desc">
          <textarea id="topic-edit-desc" v-model="editForm.description" class="topics__input" rows="3" maxlength="500" :disabled="editSubmitting" />
        </UiField>
        <UiField label="展示顺序" input-id="topic-edit-sort">
          <input id="topic-edit-sort" v-model.number="editForm.sort_order" class="topics__input" type="number" :disabled="editSubmitting" />
        </UiField>
        <UiField label="状态" input-id="topic-edit-status">
          <select id="topic-edit-status" v-model="editForm.status" class="topics__input" :disabled="editSubmitting">
            <option value="active">启用中</option>
            <option value="archived">已归档</option>
          </select>
        </UiField>
        <label class="topics__check">
          <input v-model="editForm.allow_anonymous" type="checkbox" :disabled="editSubmitting" />
          允许在该话题下匿名发帖
        </label>
        <p v-if="editFailure" class="topics__form-error" role="alert">{{ editFailure }}</p>
        <div class="topics__form-actions">
          <UiButton @click="editOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="editSubmitting" :disabled="!canSubmitEdit">保存</UiButton>
        </div>
      </form>
    </el-dialog>
  </div>
</template>

<style scoped>
.topics {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.topics__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.topics__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.topics__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.topics__alert {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.topics__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.topics__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.topics__item:hover {
  border-color: var(--cp-muted);
}

.topics__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.topics__name {
  font-size: 15px;
  color: var(--cp-ink);
}

.topics__code {
  font-size: 12px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.topics__tag {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.topics__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.topics__desc {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-muted);
  font-size: 13px;
}

.topics__actions {
  margin-top: var(--cp-space-3);
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.topics__pagination {
  display: flex;
  justify-content: center;
}

.topics__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.topics__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  color: var(--cp-ink);
  background: var(--cp-surface-card);
  box-sizing: border-box;
}

textarea.topics__input {
  resize: vertical;
}

.topics__check {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.topics__form-error {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.topics__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
}
</style>
