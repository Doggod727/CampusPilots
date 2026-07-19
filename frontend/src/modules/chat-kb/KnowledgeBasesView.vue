<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  getKnowledgeBase,
  listKnowledgeBases,
  listUsers,
  updateKnowledgeBase,
} from '@/api/generated'
import type { KnowledgeBase, KnowledgeBaseVisibility, UserSummary } from '@/api/generated'
import { useResourceList } from '@/shared/lib/useResourceList'
import { EmptyState, ErrorState, PageHeader, UiButton, UiCard, UiField, UiPagination, UiSkeleton } from '@/shared/ui'

const router = useRouter()

const VISIBILITY_FILTERS: Array<{ value: KnowledgeBaseVisibility | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'public', label: '公开' },
  { value: 'department', label: '部门' },
  { value: 'private', label: '私有' },
]
const VISIBILITY_LABELS: Record<KnowledgeBaseVisibility, string> = {
  public: '公开',
  department: '部门',
  private: '私有',
}
const ACCESS_LABELS: Record<string, string> = { viewer: '只读', editor: '可编辑', owner: '所有者' }

const searchInput = ref('')
const searchQuery = ref('')
const visibilityFilter = ref<KnowledgeBaseVisibility | ''>('')

const list = useResourceList<KnowledgeBase>(async (page, pageSize) => {
  const response = await callApi(() =>
    listKnowledgeBases({
      query: {
        page,
        page_size: pageSize,
        ...(searchQuery.value ? { q: searchQuery.value } : {}),
        ...(visibilityFilter.value ? { visibility: visibilityFilter.value as KnowledgeBaseVisibility } : {}),
      },
    }),
  )
  return { items: response.data.items, total: response.data.pagination.total }
}, 10)

async function applySearch() {
  searchQuery.value = searchInput.value.trim()
  list.page.value = 1
  await list.load()
}

async function changeVisibility(value: KnowledgeBaseVisibility | '') {
  visibilityFilter.value = value
  list.page.value = 1
  await list.load()
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function describeKbError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '权限不足：需要知识库写权限（knowledge:write）。'
    }
    if (error.status === 404) {
      return '知识库不存在或已被删除。'
    }
    if (error.status === 409) {
      if (error.code === 'KNOWLEDGE_BASE_IN_USE') {
        return '知识库仍被占用：存在未删除文档或会话引用，无法删除。'
      }
      if (error.code === 'DUPLICATE_RESOURCE') {
        return '同名知识库已存在，请更换名称。'
      }
      return '数据已被他人修改（版本冲突），请刷新列表后重试。'
    }
    if (error.status === 422) {
      return error.details[0]?.reason ?? '输入校验失败，请检查字段。'
    }
    if (error.status === 429) {
      return '请求过于频繁，请稍后再试。'
    }
  }
  return fallback
}

/* ---------- 创建 ---------- */

const showCreate = ref(false)
const createForm = reactive({ name: '', description: '', visibility: 'private' as KnowledgeBaseVisibility, owner_department: '' })
const creating = ref(false)
const createError = ref('')
/** 同一表单会话固定幂等键：失败重试复用，成功后更换。 */
const createKey = ref(crypto.randomUUID())

async function submitCreate() {
  if (!createForm.name.trim() || creating.value) {
    return
  }
  creating.value = true
  createError.value = ''
  try {
    await callApi(() =>
      createKnowledgeBase({
        body: {
          name: createForm.name.trim(),
          description: createForm.description.trim() || null,
          visibility: createForm.visibility,
          owner_department: createForm.owner_department.trim() || null,
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createKey.value = crypto.randomUUID()
    createForm.name = ''
    createForm.description = ''
    createForm.owner_department = ''
    showCreate.value = false
    await list.load()
  } catch (error) {
    createError.value = describeKbError(error, '创建失败，请稍后重试。')
  } finally {
    creating.value = false
  }
}

/* ---------- 编辑（乐观锁 version） ---------- */

const editingId = ref<string | null>(null)
const editForm = reactive({
  name: '',
  description: '',
  visibility: 'private' as KnowledgeBaseVisibility,
  owner_department: '',
  chunk_size: 512,
  chunk_overlap: 64,
  version: 0,
})
const updating = ref(false)
const updateError = ref('')

function openEdit(kb: KnowledgeBase) {
  editingId.value = kb.id
  editForm.name = kb.name
  editForm.description = kb.description ?? ''
  editForm.visibility = kb.visibility
  editForm.owner_department = kb.owner_department ?? ''
  editForm.chunk_size = kb.chunk_size
  editForm.chunk_overlap = kb.chunk_overlap
  editForm.version = kb.version
  updateError.value = ''
}

async function submitEdit() {
  if (!editingId.value || !editForm.name.trim() || updating.value) {
    return
  }
  updating.value = true
  updateError.value = ''
  try {
    await callApi(() =>
      updateKnowledgeBase({
        path: { knowledge_base_id: editingId.value as string },
        body: {
          name: editForm.name.trim(),
          description: editForm.description.trim() || null,
          visibility: editForm.visibility,
          owner_department: editForm.owner_department.trim() || null,
          chunk_size: editForm.chunk_size,
          chunk_overlap: editForm.chunk_overlap,
          version: editForm.version,
        },
      }),
    )
    editingId.value = null
    await list.load()
  } catch (error) {
    updateError.value = describeKbError(error, '更新失败，请稍后重试。')
  } finally {
    updating.value = false
  }
}

/* ---------- 删除（占用错误映射） ---------- */

const confirmingDeleteId = ref<string | null>(null)
const deleting = ref(false)
const deleteError = ref('')

async function confirmDelete(kb: KnowledgeBase) {
  if (deleting.value) {
    return
  }
  deleting.value = true
  deleteError.value = ''
  try {
    await callApi(() => deleteKnowledgeBase({ path: { knowledge_base_id: kb.id } }))
    confirmingDeleteId.value = null
    if (membersKb.value?.id === kb.id) {
      membersKb.value = null
    }
    if (editingId.value === kb.id) {
      editingId.value = null
    }
    await list.load()
  } catch (error) {
    deleteError.value = describeKbError(error, '删除失败，请稍后重试。')
  } finally {
    deleting.value = false
  }
}

/* ---------- 成员管理（经 updateKnowledgeBase 全量替换 member_user_ids） ---------- */

const membersKb = ref<KnowledgeBase | null>(null)
const membersLoading = ref(false)
const membersError = ref('')
const memberActionError = ref('')
const userQuery = ref('')
const userResults = ref<UserSummary[]>([])
const searchingUsers = ref(false)
const userSearchError = ref('')
const memberBusy = ref(false)
/** 搜索过的用户显示名缓存：成员列表只含 user_id，用于友好展示。 */
const userNames = ref<Record<string, string>>({})

async function openMembers(kb: KnowledgeBase) {
  membersLoading.value = true
  membersError.value = ''
  memberActionError.value = ''
  userQuery.value = ''
  userResults.value = []
  try {
    const response = await callApi(() => getKnowledgeBase({ path: { knowledge_base_id: kb.id } }))
    membersKb.value = response.data
  } catch (error) {
    membersError.value = describeKbError(error, '成员信息加载失败。')
    membersKb.value = null
  } finally {
    membersLoading.value = false
  }
}

async function searchUsers() {
  const q = userQuery.value.trim()
  if (!q || searchingUsers.value) {
    return
  }
  searchingUsers.value = true
  userSearchError.value = ''
  try {
    const response = await callApi(() => listUsers({ query: { q, page: 1, page_size: 10 } }))
    userResults.value = response.data.items
    for (const user of response.data.items) {
      userNames.value[user.id] = user.display_name || user.username
    }
  } catch (error) {
    userResults.value = []
    userSearchError.value =
      error instanceof ApiError && error.status === 403
        ? '当前账号无用户查询权限（user:read），无法检索用户。'
        : '用户检索失败，请稍后重试。'
  } finally {
    searchingUsers.value = false
  }
}

async function replaceMembers(nextIds: string[], version: number) {
  if (!membersKb.value || memberBusy.value) {
    return
  }
  memberBusy.value = true
  memberActionError.value = ''
  try {
    const response = await callApi(() =>
      updateKnowledgeBase({
        path: { knowledge_base_id: (membersKb.value as KnowledgeBase).id },
        body: { member_user_ids: nextIds, version },
      }),
    )
    membersKb.value = response.data
    await list.load()
  } catch (error) {
    memberActionError.value = describeKbError(error, '成员变更失败，请稍后重试。')
    if (error instanceof ApiError && error.status === 409) {
      // 版本冲突：重新拉取最新详情，便于用户基于新版本重试
      try {
        const fresh = await callApi(() =>
          getKnowledgeBase({ path: { knowledge_base_id: (membersKb.value as KnowledgeBase).id } }),
        )
        membersKb.value = fresh.data
      } catch {
        /* 保留原错误提示 */
      }
    }
  } finally {
    memberBusy.value = false
  }
}

async function addMember(user: UserSummary) {
  const kb = membersKb.value
  if (!kb) {
    return
  }
  if (kb.members.some((member) => member.user_id === user.id)) {
    memberActionError.value = '该用户已是成员。'
    return
  }
  userNames.value[user.id] = user.display_name || user.username
  await replaceMembers([...kb.members.map((member) => member.user_id), user.id], kb.version)
}

async function removeMember(userId: string) {
  const kb = membersKb.value
  if (!kb) {
    return
  }
  await replaceMembers(
    kb.members.map((member) => member.user_id).filter((id) => id !== userId),
    kb.version,
  )
}

function memberLabel(userId: string): string {
  return userNames.value[userId] ?? `用户 ${userId.slice(0, 8)}…`
}
</script>

<template>
  <div class="kb">
    <PageHeader title="知识库管理" subtitle="管理你可访问的知识库、成员与切分设置">
      <UiButton variant="primary" @click="showCreate = !showCreate">{{ showCreate ? '收起' : '新建知识库' }}</UiButton>
    </PageHeader>

    <div class="kb__toolbar">
      <form class="kb__search" @submit.prevent="applySearch">
        <input v-model="searchInput" class="kb__search-input" type="search" maxlength="100" placeholder="按名称搜索" aria-label="按名称搜索" />
        <UiButton type="submit">搜索</UiButton>
      </form>
      <div class="kb__filters" role="tablist" aria-label="可见性筛选">
        <button
          v-for="filter in VISIBILITY_FILTERS"
          :key="filter.label"
          type="button"
          class="kb__filter"
          :class="{ 'kb__filter--active': visibilityFilter === filter.value }"
          @click="changeVisibility(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
    </div>

    <UiCard v-if="showCreate" class="kb__panel" padding="md">
      <h2 class="kb__panel-title">新建知识库</h2>
      <form class="kb__form" @submit.prevent="submitCreate">
        <UiField label="名称" input-id="kb-create-name" required>
          <input id="kb-create-name" v-model="createForm.name" class="kb__input" maxlength="100" :disabled="creating" />
        </UiField>
        <UiField label="描述" input-id="kb-create-desc">
          <textarea id="kb-create-desc" v-model="createForm.description" class="kb__input" rows="2" maxlength="500" :disabled="creating" />
        </UiField>
        <div class="kb__form-row">
          <UiField label="可见性" input-id="kb-create-visibility">
            <select id="kb-create-visibility" v-model="createForm.visibility" class="kb__input" :disabled="creating">
              <option v-for="option in VISIBILITY_FILTERS.slice(1)" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </UiField>
          <UiField label="所属部门" input-id="kb-create-dept" hint="可见性为“部门”时填写">
            <input id="kb-create-dept" v-model="createForm.owner_department" class="kb__input" maxlength="100" :disabled="creating" />
          </UiField>
        </div>
        <p v-if="createError" class="kb__error" role="alert">{{ createError }}</p>
        <div class="kb__form-actions">
          <UiButton variant="primary" type="submit" :loading="creating" :disabled="!createForm.name.trim()">创建</UiButton>
        </div>
      </form>
    </UiCard>

    <UiCard v-if="editingId" class="kb__panel" padding="md">
      <h2 class="kb__panel-title">编辑知识库</h2>
      <form class="kb__form" @submit.prevent="submitEdit">
        <UiField label="名称" input-id="kb-edit-name" required>
          <input id="kb-edit-name" v-model="editForm.name" class="kb__input" maxlength="100" :disabled="updating" />
        </UiField>
        <UiField label="描述" input-id="kb-edit-desc">
          <textarea id="kb-edit-desc" v-model="editForm.description" class="kb__input" rows="2" maxlength="500" :disabled="updating" />
        </UiField>
        <div class="kb__form-row">
          <UiField label="可见性" input-id="kb-edit-visibility">
            <select id="kb-edit-visibility" v-model="editForm.visibility" class="kb__input" :disabled="updating">
              <option v-for="option in VISIBILITY_FILTERS.slice(1)" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </UiField>
          <UiField label="所属部门" input-id="kb-edit-dept">
            <input id="kb-edit-dept" v-model="editForm.owner_department" class="kb__input" maxlength="100" :disabled="updating" />
          </UiField>
        </div>
        <div class="kb__form-row">
          <UiField label="切分长度" input-id="kb-edit-chunk" hint="只影响后续入库文档">
            <input id="kb-edit-chunk" v-model.number="editForm.chunk_size" class="kb__input" type="number" min="128" max="4096" :disabled="updating" />
          </UiField>
          <UiField label="切分重叠" input-id="kb-edit-overlap">
            <input id="kb-edit-overlap" v-model.number="editForm.chunk_overlap" class="kb__input" type="number" min="0" max="1024" :disabled="updating" />
          </UiField>
        </div>
        <p v-if="updateError" class="kb__error" role="alert">{{ updateError }}</p>
        <div class="kb__form-actions">
          <UiButton variant="primary" type="submit" :loading="updating" :disabled="!editForm.name.trim()">保存</UiButton>
          <UiButton @click="editingId = null">取消</UiButton>
        </div>
      </form>
    </UiCard>

    <UiCard v-if="membersKb || membersLoading || membersError" class="kb__panel" padding="md">
      <h2 class="kb__panel-title">成员管理{{ membersKb ? `：${membersKb.name}` : '' }}</h2>
      <UiSkeleton v-if="membersLoading" :lines="3" />
      <p v-else-if="membersError" class="kb__error" role="alert">{{ membersError }}</p>
      <template v-else-if="membersKb">
        <ul class="kb__members">
          <li v-for="member in membersKb.members" :key="member.user_id" class="kb__member">
            <span class="kb__member-name">{{ memberLabel(member.user_id) }}</span>
            <span class="kb__member-meta">{{ ACCESS_LABELS[member.access_level] ?? member.access_level }}</span>
            <UiButton size="sm" :disabled="memberBusy" @click="removeMember(member.user_id)">移除</UiButton>
          </li>
          <li v-if="membersKb.members.length === 0" class="kb__member-empty">暂无成员</li>
        </ul>
        <form class="kb__member-search" @submit.prevent="searchUsers">
          <input v-model="userQuery" class="kb__input" type="search" maxlength="100" placeholder="按用户名、姓名或邮箱搜索用户" aria-label="搜索用户" />
          <UiButton type="submit" :loading="searchingUsers">搜索用户</UiButton>
        </form>
        <p v-if="userSearchError" class="kb__error" role="alert">{{ userSearchError }}</p>
        <ul v-if="userResults.length > 0" class="kb__user-results">
          <li v-for="user in userResults" :key="user.id" class="kb__member">
            <span class="kb__member-name">{{ user.display_name || user.username }}</span>
            <span class="kb__member-meta">{{ user.username }}<template v-if="user.department"> · {{ user.department }}</template></span>
            <UiButton size="sm" variant="primary" :disabled="memberBusy" @click="addMember(user)">添加</UiButton>
          </li>
        </ul>
        <p v-if="memberActionError" class="kb__error" role="alert">{{ memberActionError }}</p>
        <div class="kb__form-actions">
          <UiButton @click="membersKb = null">关闭</UiButton>
        </div>
      </template>
    </UiCard>

    <p v-if="deleteError" class="kb__error" role="alert">{{ deleteError }}</p>

    <UiSkeleton v-if="list.loading.value" :lines="5" />
    <ErrorState v-else-if="list.failed.value" title="知识库列表加载失败" @retry="list.load" />
    <EmptyState v-else-if="list.isEmpty.value" title="暂无知识库" description="点击右上角“新建知识库”创建第一个知识库" />
    <template v-else>
      <div class="kb__list">
        <UiCard v-for="kb in list.items.value" :key="kb.id" class="kb__item" padding="md">
          <div class="kb__item-head">
            <h3 class="kb__item-name">{{ kb.name }}</h3>
            <span class="kb__chip">{{ VISIBILITY_LABELS[kb.visibility] }}</span>
          </div>
          <p v-if="kb.description" class="kb__desc">{{ kb.description }}</p>
          <p class="kb__meta">
            文档 {{ kb.document_count }} · 成员 {{ kb.members.length }}<template v-if="kb.owner_department"> · {{ kb.owner_department }}</template>
            · 更新于 {{ formatTime(kb.updated_at) }} · v{{ kb.version }}
          </p>
          <div class="kb__item-actions">
            <UiButton size="sm" @click="openMembers(kb)">成员</UiButton>
            <UiButton size="sm" @click="openEdit(kb)">编辑</UiButton>
            <UiButton size="sm" @click="router.push({ name: 'knowledge-ingestion', query: { kb: kb.id } })">文档</UiButton>
            <template v-if="confirmingDeleteId === kb.id">
              <span class="kb__confirm-text">确认删除？</span>
              <UiButton size="sm" variant="danger" :loading="deleting" @click="confirmDelete(kb)">确认删除</UiButton>
              <UiButton size="sm" @click="confirmingDeleteId = null">取消</UiButton>
            </template>
            <UiButton v-else size="sm" variant="danger" @click="confirmingDeleteId = kb.id; deleteError = ''">删除</UiButton>
          </div>
        </UiCard>
      </div>
      <div class="kb__pagination">
        <UiPagination :page="list.page.value" :total="list.total.value" :page-size="list.pageSize" @change="list.changePage" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.kb {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.kb__toolbar {
  display: flex;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
}

.kb__search {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.kb__search-input,
.kb__input {
  min-height: var(--cp-control-md);
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  color: var(--cp-ink);
  font-family: var(--cp-font-sans);
  font-size: 14px;
}

textarea.kb__input {
  padding: var(--cp-space-2) var(--cp-space-3);
  resize: vertical;
}

.kb__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.kb__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.kb__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.kb__panel {
  max-width: 720px;
}

.kb__panel-title {
  margin: 0 0 var(--cp-space-3);
  font-size: 15px;
  color: var(--cp-ink);
}

.kb__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.kb__form-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--cp-space-3);
}

.kb__form-actions {
  display: flex;
  gap: var(--cp-space-2);
}

.kb__error {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.kb__members,
.kb__user-results {
  margin: 0 0 var(--cp-space-3);
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.kb__member {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-button);
}

.kb__member-name {
  font-size: 14px;
  color: var(--cp-ink);
}

.kb__member-meta {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted);
}

.kb__member-empty {
  font-size: 13px;
  color: var(--cp-muted);
}

.kb__member-search {
  display: flex;
  gap: var(--cp-space-2);
  margin-bottom: var(--cp-space-2);
}

.kb__member-search .kb__input {
  flex: 1;
}

.kb__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.kb__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.kb__item-name {
  margin: 0;
  font-size: 16px;
  color: var(--cp-ink);
}

.kb__chip {
  padding: 2px 10px;
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-pill);
  font-size: 12px;
  color: var(--cp-muted);
  background: var(--cp-canvas-soft);
}

.kb__desc {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-body);
}

.kb__meta {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.kb__item-actions {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-3);
  flex-wrap: wrap;
}

.kb__confirm-text {
  font-size: 13px;
  color: var(--cp-error);
}

.kb__pagination {
  display: flex;
  justify-content: center;
}
</style>
