<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { callApi } from '@/api/client'
import { createUser, getUser, listRoles, listUsers, replaceUserRoles, updateUser } from '@/api/generated'
import type { Role, UserStatus, UserSummary } from '@/api/generated'
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

import { describeApiError, formatTime, type Failure } from './admin-utils'

const STATUS_FILTERS: Array<{ value: UserStatus | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'active', label: '启用' },
  { value: 'disabled', label: '停用' },
  { value: 'locked', label: '锁定' },
]

const STATUS_LABEL: Record<UserStatus, string> = { active: '启用', disabled: '停用', locked: '锁定' }
const USERNAME_PATTERN = /^[a-zA-Z][a-zA-Z0-9_.-]{2,49}$/

// ---------- 列表 ----------
const keyword = ref('')
const statusFilter = ref<UserStatus | ''>('')

const { items, total, page, pageSize, loading, failed, isEmpty, load, changePage } = useResourceList<UserSummary>(
  async (currentPage, size) => {
    const response = await callApi(() =>
      listUsers({
        query: {
          page: currentPage,
          page_size: size,
          ...(keyword.value.trim() ? { q: keyword.value.trim() } : {}),
          ...(statusFilter.value ? { status: statusFilter.value as UserStatus } : {}),
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

async function changeStatusFilter(value: UserStatus | '') {
  statusFilter.value = value
  await applyFilters()
}

// ---------- 角色目录（新建 / 分配共用，来自后端角色列表） ----------
const roles = ref<Role[]>([])
const rolesFailed = ref(false)

async function loadRoles() {
  rolesFailed.value = false
  try {
    const response = await callApi(() => listRoles())
    roles.value = response.data.items
  } catch {
    rolesFailed.value = true
  }
}

onMounted(loadRoles)

// ---------- 新建用户 ----------
const createOpen = ref(false)
const createForm = reactive({
  username: '',
  password: '',
  display_name: '',
  email: '',
  department: '',
  role_ids: [] as string[],
})
const createSubmitting = ref(false)
const createFailure = ref<Failure | null>(null)
/** 同一表单会话固定幂等键：重试复用，成功后才轮换。 */
const createKey = ref(crypto.randomUUID())

const canCreate = computed(
  () =>
    USERNAME_PATTERN.test(createForm.username.trim()) &&
    createForm.password.length >= 10 &&
    createForm.display_name.trim().length > 0 &&
    createForm.role_ids.length > 0 &&
    !createSubmitting.value,
)

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
      createUser({
        body: {
          username: createForm.username.trim(),
          password: createForm.password,
          display_name: createForm.display_name.trim(),
          email: createForm.email.trim() || null,
          department: createForm.department.trim() || null,
          role_ids: [...createForm.role_ids],
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createKey.value = crypto.randomUUID()
    createForm.username = ''
    createForm.password = ''
    createForm.display_name = ''
    createForm.email = ''
    createForm.department = ''
    createForm.role_ids = []
    createOpen.value = false
    await load()
  } catch (error) {
    createFailure.value = describeApiError(error, '创建用户失败', {
      DUPLICATE_RESOURCE: '用户名已存在，请更换后重试。',
      ROLE_NOT_FOUND: '所选角色不存在，请刷新角色目录。',
    })
  } finally {
    createSubmitting.value = false
  }
}

// ---------- 详情 / 编辑 ----------
const selectedId = ref<string | null>(null)
const detail = ref<UserSummary | null>(null)
const detailLoading = ref(false)
const detailFailed = ref(false)

const editForm = reactive({ display_name: '', email: '', department: '' })
const assignIds = ref<string[]>([])

const profileSubmitting = ref(false)
const profileFailure = ref<Failure | null>(null)
const profileNotice = ref('')
const statusSubmitting = ref(false)
const statusFailure = ref<Failure | null>(null)
const rolesSubmitting = ref(false)
const rolesFailure = ref<Failure | null>(null)

async function openDetail(userId: string) {
  selectedId.value = userId
  detailLoading.value = true
  detailFailed.value = false
  detail.value = null
  profileFailure.value = null
  profileNotice.value = ''
  statusFailure.value = null
  rolesFailure.value = null
  try {
    const response = await callApi(() => getUser({ path: { user_id: userId } }))
    detail.value = response.data
    editForm.display_name = response.data.display_name
    editForm.email = response.data.email ?? ''
    editForm.department = response.data.department ?? ''
    assignIds.value = response.data.roles.map((role) => role.id)
  } catch {
    detailFailed.value = true
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  selectedId.value = null
  detail.value = null
}

async function retryDetail() {
  if (selectedId.value) {
    await openDetail(selectedId.value)
  }
}

async function saveProfile() {
  const current = detail.value
  if (!current || profileSubmitting.value || !editForm.display_name.trim()) {
    return
  }
  profileSubmitting.value = true
  profileFailure.value = null
  profileNotice.value = ''
  try {
    const response = await callApi(() =>
      updateUser({
        path: { user_id: current.id },
        body: {
          display_name: editForm.display_name.trim(),
          email: editForm.email.trim() || null,
          department: editForm.department.trim() || null,
          version: current.version,
        },
      }),
    )
    detail.value = response.data
    profileNotice.value = '基本资料已保存。'
    await load()
  } catch (error) {
    profileFailure.value = describeApiError(error, '保存资料失败', {
      RESOURCE_VERSION_CONFLICT: '数据已被其他操作更新，请关闭后重新打开详情再试。',
    })
  } finally {
    profileSubmitting.value = false
  }
}

async function changeStatus(status: 'active' | 'disabled') {
  const current = detail.value
  if (!current || statusSubmitting.value || current.status === status) {
    return
  }
  statusSubmitting.value = true
  statusFailure.value = null
  try {
    const response = await callApi(() =>
      updateUser({ path: { user_id: current.id }, body: { status, version: current.version } }),
    )
    detail.value = response.data
    await load()
  } catch (error) {
    statusFailure.value = describeApiError(error, '状态变更失败', {
      LAST_SUPER_ADMIN: '不能停用最后一个有效超级管理员。',
      STATUS_CHANGE_NOT_ALLOWED: '该账号状态不能由此接口设置。',
      RESOURCE_VERSION_CONFLICT: '数据已被其他操作更新，请关闭后重新打开详情再试。',
    })
  } finally {
    statusSubmitting.value = false
  }
}

async function saveRoles() {
  const current = detail.value
  if (!current || rolesSubmitting.value || assignIds.value.length === 0) {
    return
  }
  rolesSubmitting.value = true
  rolesFailure.value = null
  try {
    const response = await callApi(() =>
      replaceUserRoles({
        path: { user_id: current.id },
        body: { role_ids: [...assignIds.value], version: current.version },
      }),
    )
    detail.value = response.data
    await load()
  } catch (error) {
    rolesFailure.value = describeApiError(error, '角色分配失败', {
      ROLE_NOT_FOUND: '所选角色不存在，请刷新角色目录。',
      LAST_SUPER_ADMIN: '不能移除最后一个有效超级管理员的角色。',
      RESOURCE_VERSION_CONFLICT: '数据已被其他操作更新，请关闭后重新打开详情再试。',
    })
  } finally {
    rolesSubmitting.value = false
  }
}
</script>

<template>
  <div class="users">
    <PageHeader title="用户管理" subtitle="账号资料、状态与角色分配；所有变更以服务端结果为准">
      <UiButton variant="primary" @click="toggleCreate">{{ createOpen ? '取消新建' : '新建用户' }}</UiButton>
    </PageHeader>

    <UiCard v-if="createOpen" padding="md" class="users__panel">
      <h2 class="users__panel-title">新建用户</h2>
      <form class="users__form" @submit.prevent="submitCreate">
        <div class="users__grid">
          <UiField label="用户名" input-id="user-create-username" required hint="字母开头，3–50 位字母/数字/_.-">
            <input
              id="user-create-username"
              v-model="createForm.username"
              class="users__input"
              maxlength="50"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="初始密码" input-id="user-create-password" required hint="至少 10 位；创建后不在页面展示">
            <input
              id="user-create-password"
              v-model="createForm.password"
              class="users__input"
              type="password"
              autocomplete="new-password"
              maxlength="128"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="显示名" input-id="user-create-display" required>
            <input
              id="user-create-display"
              v-model="createForm.display_name"
              class="users__input"
              maxlength="50"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="邮箱" input-id="user-create-email">
            <input
              id="user-create-email"
              v-model="createForm.email"
              class="users__input"
              type="email"
              maxlength="254"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="部门" input-id="user-create-department">
            <input
              id="user-create-department"
              v-model="createForm.department"
              class="users__input"
              maxlength="100"
              :disabled="createSubmitting"
            />
          </UiField>
        </div>
        <UiField label="角色" required hint="至少选择一个角色">
          <p v-if="rolesFailed" class="users__inline-error">
            角色目录加载失败。
            <button type="button" class="users__link" @click="loadRoles">重试</button>
          </p>
          <div v-else class="users__checks">
            <label v-for="role in roles" :key="role.id" class="users__check">
              <input v-model="createForm.role_ids" type="checkbox" :value="role.id" :disabled="createSubmitting" />
              <span>{{ role.name }}（{{ role.code }}）</span>
            </label>
          </div>
        </UiField>
        <p v-if="createFailure" class="users__failure" role="alert">
          <strong>{{ createFailure.title }}</strong>
          <span>{{ createFailure.message }}</span>
        </p>
        <div class="users__actions">
          <UiButton variant="primary" type="submit" :loading="createSubmitting" :disabled="!canCreate">
            创建用户
          </UiButton>
        </div>
      </form>
    </UiCard>

    <div class="users__toolbar">
      <form class="users__search" @submit.prevent="applyFilters">
        <input v-model="keyword" class="users__input" placeholder="按用户名 / 显示名 / 邮箱搜索" aria-label="搜索用户" />
        <UiButton type="submit">搜索</UiButton>
      </form>
      <div class="users__filters" role="tablist">
        <button
          v-for="filter in STATUS_FILTERS"
          :key="filter.label"
          type="button"
          class="users__filter"
          :class="{ 'users__filter--active': statusFilter === filter.value }"
          @click="changeStatusFilter(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
    </div>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="用户列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无用户" description="调整筛选条件，或新建一个用户" />
    <template v-else>
      <UiCard padding="none" class="users__table-card">
        <table class="users__table">
          <thead>
            <tr>
              <th>用户名</th>
              <th>显示名</th>
              <th>状态</th>
              <th>角色</th>
              <th>最近登录</th>
              <th class="users__col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in items" :key="user.id" :class="{ 'users__row--selected': selectedId === user.id }">
              <td>
                <span class="users__username">{{ user.username }}</span>
              </td>
              <td>{{ user.display_name }}</td>
              <td><StatusBadge :status="user.status" :label="STATUS_LABEL[user.status]" /></td>
              <td>
                <span class="users__roles">{{ user.roles.map((role) => role.name).join('、') || '—' }}</span>
              </td>
              <td>
                <time class="users__time">{{ formatTime(user.last_login_at) }}</time>
              </td>
              <td class="users__col-actions">
                <UiButton size="sm" @click="openDetail(user.id)">管理</UiButton>
              </td>
            </tr>
          </tbody>
        </table>
      </UiCard>
      <div class="users__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <UiCard v-if="selectedId" padding="md" class="users__panel">
      <UiSkeleton v-if="detailLoading" :lines="4" />
      <ErrorState v-else-if="detailFailed" title="用户详情加载失败" @retry="retryDetail" />
      <div v-else-if="detail" class="users__detail">
        <div class="users__detail-head">
          <h2 class="users__panel-title">{{ detail.display_name }}（{{ detail.username }}）</h2>
          <StatusBadge :status="detail.status" :label="STATUS_LABEL[detail.status]" />
          <UiButton size="sm" class="users__detail-close" @click="closeDetail">关闭</UiButton>
        </div>
        <p class="users__meta">
          邮箱 {{ detail.email || '—' }} · 部门 {{ detail.department || '—' }} · 注册于
          {{ formatTime(detail.created_at) }}
        </p>

        <section class="users__section">
          <h3 class="users__section-title">基本资料</h3>
          <form class="users__form" @submit.prevent="saveProfile">
            <div class="users__grid">
              <UiField label="显示名" input-id="user-edit-display" required>
                <input
                  id="user-edit-display"
                  v-model="editForm.display_name"
                  class="users__input"
                  maxlength="50"
                  :disabled="profileSubmitting"
                />
              </UiField>
              <UiField label="邮箱" input-id="user-edit-email">
                <input
                  id="user-edit-email"
                  v-model="editForm.email"
                  class="users__input"
                  type="email"
                  maxlength="254"
                  :disabled="profileSubmitting"
                />
              </UiField>
              <UiField label="部门" input-id="user-edit-department">
                <input
                  id="user-edit-department"
                  v-model="editForm.department"
                  class="users__input"
                  maxlength="100"
                  :disabled="profileSubmitting"
                />
              </UiField>
            </div>
            <p v-if="profileFailure" class="users__failure" role="alert">
              <strong>{{ profileFailure.title }}</strong>
              <span>{{ profileFailure.message }}</span>
            </p>
            <p v-if="profileNotice" class="users__notice" role="status">{{ profileNotice }}</p>
            <div class="users__actions">
              <UiButton variant="primary" type="submit" :loading="profileSubmitting" :disabled="!editForm.display_name.trim()">
                保存资料
              </UiButton>
            </div>
          </form>
        </section>

        <section class="users__section">
          <h3 class="users__section-title">账号状态</h3>
          <div class="users__actions">
            <UiButton size="sm" :disabled="detail.status === 'active'" :loading="statusSubmitting" @click="changeStatus('active')">
              启用
            </UiButton>
            <UiButton
              size="sm"
              variant="danger"
              :disabled="detail.status === 'disabled'"
              :loading="statusSubmitting"
              @click="changeStatus('disabled')"
            >
              停用
            </UiButton>
          </div>
          <p v-if="statusFailure" class="users__failure" role="alert">
            <strong>{{ statusFailure.title }}</strong>
            <span>{{ statusFailure.message }}</span>
          </p>
        </section>

        <section class="users__section">
          <h3 class="users__section-title">角色分配（全量替换）</h3>
          <div class="users__checks">
            <label v-for="role in roles" :key="role.id" class="users__check">
              <input v-model="assignIds" type="checkbox" :value="role.id" :disabled="rolesSubmitting" />
              <span>{{ role.name }}（{{ role.code }}）</span>
            </label>
          </div>
          <p v-if="rolesFailure" class="users__failure" role="alert">
            <strong>{{ rolesFailure.title }}</strong>
            <span>{{ rolesFailure.message }}</span>
          </p>
          <div class="users__actions">
            <UiButton variant="primary" size="sm" :loading="rolesSubmitting" :disabled="assignIds.length === 0" @click="saveRoles">
              保存角色
            </UiButton>
          </div>
        </section>
      </div>
    </UiCard>
  </div>
</template>

<style scoped>
.users {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.users__panel-title {
  margin: 0 0 var(--cp-space-3);
  font-size: 15px;
  color: var(--cp-ink);
}

.users__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.users__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--cp-space-3);
}

.users__input {
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

.users__checks {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cp-space-2) var(--cp-space-4);
}

.users__check {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.users__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.users__search {
  display: flex;
  gap: var(--cp-space-2);
  flex: 1;
  min-width: 260px;
  max-width: 480px;
}

.users__filters {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.users__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.users__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.users__table-card {
  overflow-x: auto;
}

.users__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.users__table th,
.users__table td {
  padding: var(--cp-space-2) var(--cp-space-3);
  text-align: left;
  border-bottom: 1px solid var(--cp-hairline-soft);
  vertical-align: middle;
}

.users__table th {
  color: var(--cp-muted);
  font-weight: 500;
  white-space: nowrap;
}

.users__table tbody tr:last-child td {
  border-bottom: none;
}

.users__row--selected td {
  background: var(--cp-canvas-soft);
}

.users__username {
  font-family: var(--cp-font-mono);
  font-size: 12px;
  color: var(--cp-ink);
}

.users__roles,
.users__time {
  color: var(--cp-muted);
}

.users__col-actions {
  text-align: right;
  white-space: nowrap;
}

.users__pagination {
  display: flex;
  justify-content: center;
}

.users__detail {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.users__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
}

.users__detail-head .users__panel-title {
  margin: 0;
}

.users__detail-close {
  margin-left: auto;
}

.users__meta {
  margin: 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.users__section {
  border-top: 1px solid var(--cp-hairline-soft);
  padding-top: var(--cp-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.users__section-title {
  margin: 0;
  font-size: 14px;
  color: var(--cp-ink);
}

.users__actions {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.users__failure {
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

.users__notice {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-success) 6%, white);
  color: var(--cp-success);
  font-size: 13px;
}

.users__inline-error {
  margin: 0;
  font-size: 13px;
  color: var(--cp-error);
}

.users__link {
  border: none;
  background: none;
  color: var(--cp-primary);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
}
</style>
