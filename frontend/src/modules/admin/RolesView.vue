<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { callApi } from '@/api/client'
import { createRole, deleteRole, listPermissions, listRoles, replaceRolePermissions, updateRole } from '@/api/generated'
import type { Permission, Role } from '@/api/generated'
import { EmptyState, ErrorState, PageHeader, UiButton, UiCard, UiField, UiSkeleton } from '@/shared/ui'

import { describeApiError, type Failure } from './admin-utils'

const ROLE_CODE_PATTERN = /^[a-z][a-z0-9_]{2,49}$/

// ---------- 角色列表 ----------
const roles = ref<Role[]>([])
const loading = ref(true)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    const response = await callApi(() => listRoles())
    roles.value = response.data.items
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

// ---------- 权限字典 ----------
const permissions = ref<Permission[]>([])
const permissionsFailed = ref(false)

async function loadPermissions() {
  permissionsFailed.value = false
  try {
    const response = await callApi(() => listPermissions())
    permissions.value = response.data.items
  } catch {
    permissionsFailed.value = true
  }
}

const permissionGroups = computed(() => {
  const groups = new Map<string, Permission[]>()
  for (const permission of permissions.value) {
    const bucket = groups.get(permission.module) ?? []
    bucket.push(permission)
    groups.set(permission.module, bucket)
  }
  return [...groups.entries()].map(([module, items]) => ({ module, items }))
})

onMounted(() => {
  void load()
  void loadPermissions()
})

// ---------- 新建角色 ----------
const createOpen = ref(false)
const createForm = reactive({ code: '', name: '', description: '', permission_ids: [] as string[] })
const createSubmitting = ref(false)
const createFailure = ref<Failure | null>(null)
const createKey = ref(crypto.randomUUID())

const canCreate = computed(
  () =>
    ROLE_CODE_PATTERN.test(createForm.code.trim()) &&
    createForm.name.trim().length > 0 &&
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
      createRole({
        body: {
          code: createForm.code.trim(),
          name: createForm.name.trim(),
          description: createForm.description.trim() || null,
          permission_ids: [...createForm.permission_ids],
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createKey.value = crypto.randomUUID()
    createForm.code = ''
    createForm.name = ''
    createForm.description = ''
    createForm.permission_ids = []
    createOpen.value = false
    await load()
  } catch (error) {
    createFailure.value = describeApiError(error, '创建角色失败', {
      DUPLICATE_RESOURCE: '角色编码已存在，请更换后重试。',
      PERMISSION_NOT_FOUND: '所选权限不存在，请刷新权限字典。',
    })
  } finally {
    createSubmitting.value = false
  }
}

// ---------- 编辑 / 权限分配 ----------
const expandedId = ref<string | null>(null)
const editForm = reactive({ name: '', description: '' })
const assignIds = ref<string[]>([])

const infoSubmitting = ref(false)
const infoFailure = ref<Failure | null>(null)
const infoNotice = ref('')
const permSubmitting = ref(false)
const permFailure = ref<Failure | null>(null)
const permNotice = ref('')

function expandRole(role: Role) {
  if (expandedId.value === role.id) {
    expandedId.value = null
    return
  }
  expandedId.value = role.id
  editForm.name = role.name
  editForm.description = role.description ?? ''
  assignIds.value = role.permissions.map((permission) => permission.id)
  infoFailure.value = null
  infoNotice.value = ''
  permFailure.value = null
  permNotice.value = ''
}

function expandedRole(): Role | undefined {
  return roles.value.find((role) => role.id === expandedId.value)
}

async function saveInfo() {
  const current = expandedRole()
  if (!current || infoSubmitting.value || !editForm.name.trim()) {
    return
  }
  infoSubmitting.value = true
  infoFailure.value = null
  infoNotice.value = ''
  try {
    await callApi(() =>
      updateRole({
        path: { role_id: current.id },
        body: {
          name: editForm.name.trim(),
          description: editForm.description.trim() || null,
          version: current.version,
        },
      }),
    )
    infoNotice.value = '角色信息已保存。'
    await load()
  } catch (error) {
    infoFailure.value = describeApiError(error, '保存角色失败', {
      SYSTEM_ROLE_PROTECTED: '系统预置角色不可修改。',
      RESOURCE_VERSION_CONFLICT: '数据已被其他操作更新，请刷新列表后重试。',
    })
  } finally {
    infoSubmitting.value = false
  }
}

async function savePermissions() {
  const current = expandedRole()
  if (!current || permSubmitting.value) {
    return
  }
  permSubmitting.value = true
  permFailure.value = null
  permNotice.value = ''
  try {
    await callApi(() =>
      replaceRolePermissions({
        path: { role_id: current.id },
        body: { permission_ids: [...assignIds.value], version: current.version },
      }),
    )
    permNotice.value = '角色权限已保存。'
    await load()
  } catch (error) {
    permFailure.value = describeApiError(error, '保存权限失败', {
      SYSTEM_ROLE_PROTECTED: '系统预置角色的权限不可修改。',
      PERMISSION_NOT_FOUND: '所选权限不存在，请刷新权限字典。',
      RESOURCE_VERSION_CONFLICT: '数据已被其他操作更新，请刷新列表后重试。',
    })
  } finally {
    permSubmitting.value = false
  }
}

// ---------- 删除 ----------
const confirmingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const deleteFailure = ref<Failure | null>(null)

function askDelete(role: Role) {
  confirmingId.value = confirmingId.value === role.id ? null : role.id
  deleteFailure.value = null
}

async function confirmDelete(role: Role) {
  if (deletingId.value) {
    return
  }
  deletingId.value = role.id
  deleteFailure.value = null
  try {
    await callApi(() => deleteRole({ path: { role_id: role.id } }))
    confirmingId.value = null
    if (expandedId.value === role.id) {
      expandedId.value = null
    }
    await load()
  } catch (error) {
    deleteFailure.value = describeApiError(error, '删除角色失败', {
      SYSTEM_ROLE_PROTECTED: '系统预置角色不可删除。',
      ROLE_IN_USE: '角色仍被用户使用，请先调整相关用户的角色。',
    })
  } finally {
    deletingId.value = null
  }
}
</script>

<template>
  <div class="roles">
    <PageHeader title="角色权限" subtitle="角色与权限字典；权限变更为全量替换">
      <UiButton variant="primary" @click="toggleCreate">{{ createOpen ? '取消新建' : '新建角色' }}</UiButton>
    </PageHeader>

    <UiCard v-if="createOpen" padding="md" class="roles__panel">
      <h2 class="roles__panel-title">新建角色</h2>
      <form class="roles__form" @submit.prevent="submitCreate">
        <div class="roles__grid">
          <UiField label="角色编码" input-id="role-create-code" required hint="小写字母开头，3–50 位小写字母/数字/下划线">
            <input
              id="role-create-code"
              v-model="createForm.code"
              class="roles__input"
              maxlength="50"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="角色名称" input-id="role-create-name" required>
            <input
              id="role-create-name"
              v-model="createForm.name"
              class="roles__input"
              maxlength="50"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="描述" input-id="role-create-description">
            <input
              id="role-create-description"
              v-model="createForm.description"
              class="roles__input"
              maxlength="500"
              :disabled="createSubmitting"
            />
          </UiField>
        </div>
        <UiField label="初始权限">
          <div class="roles__checks">
            <label v-for="permission in permissions" :key="permission.id" class="roles__check">
              <input v-model="createForm.permission_ids" type="checkbox" :value="permission.id" :disabled="createSubmitting" />
              <span>{{ permission.name }}（{{ permission.code }}）</span>
            </label>
          </div>
        </UiField>
        <p v-if="createFailure" class="roles__failure" role="alert">
          <strong>{{ createFailure.title }}</strong>
          <span>{{ createFailure.message }}</span>
        </p>
        <div class="roles__actions">
          <UiButton variant="primary" type="submit" :loading="createSubmitting" :disabled="!canCreate">创建角色</UiButton>
        </div>
      </form>
    </UiCard>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="角色列表加载失败" @retry="load" />
    <EmptyState v-else-if="roles.length === 0" title="暂无角色" description="新建一个自定义角色" />
    <div v-else class="roles__list">
      <UiCard v-for="role in roles" :key="role.id" padding="md" class="roles__item">
        <div class="roles__item-head">
          <strong class="roles__name">{{ role.name }}</strong>
          <code class="roles__code">{{ role.code }}</code>
          <span v-if="role.is_system" class="roles__tag">系统预置</span>
          <span class="roles__count">{{ role.user_count }} 个用户</span>
          <div class="roles__item-actions">
            <UiButton size="sm" @click="expandRole(role)">{{ expandedId === role.id ? '收起' : '编辑' }}</UiButton>
            <UiButton v-if="!role.is_system" size="sm" variant="danger" @click="askDelete(role)">删除</UiButton>
          </div>
        </div>
        <p v-if="role.description" class="roles__desc">{{ role.description }}</p>
        <p class="roles__perms">权限：{{ role.permissions.map((permission) => permission.name).join('、') || '无' }}</p>

        <div v-if="confirmingId === role.id" class="roles__confirm" role="alert">
          <span>确认删除角色「{{ role.name }}」？该操作不可撤销。</span>
          <UiButton size="sm" variant="danger" :loading="deletingId === role.id" @click="confirmDelete(role)">
            确认删除
          </UiButton>
          <UiButton size="sm" :disabled="deletingId === role.id" @click="confirmingId = null">取消</UiButton>
        </div>
        <p v-if="confirmingId === role.id && deleteFailure" class="roles__failure" role="alert">
          <strong>{{ deleteFailure.title }}</strong>
          <span>{{ deleteFailure.message }}</span>
        </p>

        <div v-if="expandedId === role.id" class="roles__editor">
          <section class="roles__section">
            <h3 class="roles__section-title">基本信息</h3>
            <form class="roles__form" @submit.prevent="saveInfo">
              <div class="roles__grid">
                <UiField label="角色名称" input-id="role-edit-name" required>
                  <input
                    id="role-edit-name"
                    v-model="editForm.name"
                    class="roles__input"
                    maxlength="50"
                    :disabled="infoSubmitting"
                  />
                </UiField>
                <UiField label="描述" input-id="role-edit-description">
                  <input
                    id="role-edit-description"
                    v-model="editForm.description"
                    class="roles__input"
                    maxlength="500"
                    :disabled="infoSubmitting"
                  />
                </UiField>
              </div>
              <p v-if="infoFailure" class="roles__failure" role="alert">
                <strong>{{ infoFailure.title }}</strong>
                <span>{{ infoFailure.message }}</span>
              </p>
              <p v-if="infoNotice" class="roles__notice" role="status">{{ infoNotice }}</p>
              <div class="roles__actions">
                <UiButton variant="primary" type="submit" :loading="infoSubmitting" :disabled="!editForm.name.trim()">
                  保存信息
                </UiButton>
              </div>
            </form>
          </section>

          <section class="roles__section">
            <h3 class="roles__section-title">权限分配（全量替换）</h3>
            <p v-if="permissionsFailed" class="roles__inline-error">
              权限字典加载失败。
              <button type="button" class="roles__link" @click="loadPermissions">重试</button>
            </p>
            <div v-else class="roles__checks">
              <label v-for="permission in permissions" :key="permission.id" class="roles__check">
                <input v-model="assignIds" type="checkbox" :value="permission.id" :disabled="permSubmitting" />
                <span>{{ permission.name }}（{{ permission.code }}）</span>
              </label>
            </div>
            <p v-if="permFailure" class="roles__failure" role="alert">
              <strong>{{ permFailure.title }}</strong>
              <span>{{ permFailure.message }}</span>
            </p>
            <p v-if="permNotice" class="roles__notice" role="status">{{ permNotice }}</p>
            <div class="roles__actions">
              <UiButton variant="primary" size="sm" :loading="permSubmitting" @click="savePermissions">保存权限</UiButton>
            </div>
          </section>
        </div>
      </UiCard>
    </div>

    <UiCard padding="md" class="roles__dictionary">
      <h2 class="roles__panel-title">权限字典</h2>
      <UiSkeleton v-if="!permissionsFailed && permissions.length === 0" :lines="3" />
      <p v-else-if="permissionsFailed" class="roles__inline-error">
        权限字典加载失败。
        <button type="button" class="roles__link" @click="loadPermissions">重试</button>
      </p>
      <div v-else class="roles__groups">
        <section v-for="group in permissionGroups" :key="group.module" class="roles__group">
          <h3 class="roles__group-title">{{ group.module }}</h3>
          <ul class="roles__group-list">
            <li v-for="permission in group.items" :key="permission.id">
              <code>{{ permission.code }}</code>
              <span>{{ permission.name }}</span>
            </li>
          </ul>
        </section>
      </div>
    </UiCard>
  </div>
</template>

<style scoped>
.roles {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.roles__panel-title {
  margin: 0 0 var(--cp-space-3);
  font-size: 15px;
  color: var(--cp-ink);
}

.roles__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.roles__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--cp-space-3);
}

.roles__input {
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

.roles__checks {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cp-space-2) var(--cp-space-4);
  max-height: 220px;
  overflow-y: auto;
}

.roles__check {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.roles__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.roles__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.roles__name {
  font-size: 14px;
  color: var(--cp-ink);
}

.roles__code {
  font-family: var(--cp-font-mono);
  font-size: 12px;
  color: var(--cp-muted);
}

.roles__tag {
  padding: 2px 10px;
  border-radius: var(--cp-radius-pill);
  border: 1px solid var(--cp-hairline);
  background: var(--cp-canvas-soft);
  color: var(--cp-muted);
  font-size: 12px;
}

.roles__count {
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.roles__item-actions {
  margin-left: auto;
  display: flex;
  gap: var(--cp-space-2);
}

.roles__desc {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-body);
}

.roles__perms {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.roles__confirm {
  margin-top: var(--cp-space-3);
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-warning) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-warning) 7%, white);
  color: var(--cp-body-strong);
  font-size: 13px;
}

.roles__editor {
  margin-top: var(--cp-space-3);
  border-top: 1px solid var(--cp-hairline-soft);
  padding-top: var(--cp-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.roles__section {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.roles__section-title {
  margin: 0;
  font-size: 14px;
  color: var(--cp-ink);
}

.roles__actions {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.roles__failure {
  margin: var(--cp-space-2) 0 0;
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

.roles__notice {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-success) 6%, white);
  color: var(--cp-success);
  font-size: 13px;
}

.roles__inline-error {
  margin: 0;
  font-size: 13px;
  color: var(--cp-error);
}

.roles__link {
  border: none;
  background: none;
  color: var(--cp-primary);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
}

.roles__groups {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--cp-space-4);
}

.roles__group-title {
  margin: 0 0 var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-muted);
}

.roles__group-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.roles__group-list code {
  font-family: var(--cp-font-mono);
  font-size: 12px;
  color: var(--cp-ink);
  margin-right: var(--cp-space-2);
}

.roles__group-list span {
  color: var(--cp-body);
}
</style>
