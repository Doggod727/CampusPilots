<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { ApiError, callApi } from '@/api/client'
import {
  cancelCampusEvent,
  cancelMyEventRegistration,
  createCampusEvent,
  getCampusEvent,
  listCampusEvents,
  listEventRegistrations,
  registerCampusEvent,
  updateCampusEvent,
} from '@/api/generated'
import type { CampusEvent, CampusEventStatus, EventRegistration } from '@/api/generated'
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

const EVENT_STATUS_LABELS: Record<CampusEventStatus, string> = {
  pending_review: '审核中',
  published: '已发布',
  rejected: '已拒绝',
  cancelled: '已取消',
  ended: '已结束',
  deleted: '已删除',
}

const EVENT_CATEGORY_OPTIONS = [
  { value: 'lecture', label: '讲座' },
  { value: 'club', label: '社团活动' },
  { value: 'sports', label: '体育活动' },
  { value: 'arts', label: '文艺活动' },
  { value: 'volunteer', label: '志愿服务' },
  { value: 'competition', label: '竞赛' },
  { value: 'career', label: '就业招聘' },
  { value: 'other', label: '其他' },
] as const

function eventCategoryLabel(value: string): string {
  return EVENT_CATEGORY_OPTIONS.find((option) => option.value === value)?.label ?? value
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('community:write'))
const canModerate = computed(() => auth.hasPermission('community:moderate'))

// ---------- 列表与筛选 ----------
const filters = reactive({ category: '', startsFrom: '', startsTo: '', availableOnly: false, mine: false })
const applied = reactive({ category: '', startsFrom: '', startsTo: '', availableOnly: false, mine: false })

const {
  items: events,
  total,
  page,
  pageSize,
  loading,
  failed,
  isEmpty,
  load,
  changePage,
} = useResourceList<CampusEvent>(async (pageNum, size) => {
  const response = await callApi(() =>
    listCampusEvents({
      query: {
        page: pageNum,
        page_size: size,
        ...(applied.category ? { category: applied.category } : {}),
        ...(applied.startsFrom ? { starts_from: new Date(`${applied.startsFrom}T00:00:00`).toISOString() } : {}),
        ...(applied.startsTo ? { starts_to: new Date(`${applied.startsTo}T23:59:59.999`).toISOString() } : {}),
        ...(applied.availableOnly ? { available_only: true } : {}),
        ...(applied.mine ? { mine: true } : {}),
      },
    }),
  )
  return { items: response.data.items, total: response.data.pagination.total }
})

async function applyFilters() {
  applied.category = filters.category.trim()
  applied.startsFrom = filters.startsFrom
  applied.startsTo = filters.startsTo
  applied.availableOnly = filters.availableOnly
  applied.mine = filters.mine
  page.value = 1
  await load()
}

async function resetFilters() {
  filters.category = ''
  filters.startsFrom = ''
  filters.startsTo = ''
  filters.availableOnly = false
  filters.mine = false
  await applyFilters()
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function toInputValue(iso: string): string {
  const date = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function toIso(local: string): string {
  return new Date(local).toISOString()
}

function isFull(event: CampusEvent): boolean {
  return event.registered_count >= event.capacity
}

function describeEventError(error: unknown, fallback: string): { title: string; message: string } {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'EVENT_CAPACITY_FULL':
        return { title: '名额已满', message: '活动名额已满，无法报名。' }
      case 'EVENT_REGISTRATION_CLOSED':
        return { title: '报名已关闭', message: '已过报名截止时间或活动已开始，无法报名/取消。' }
      case 'EVENT_REGISTRATION_BUSY':
        return { title: '报名繁忙', message: '报名人数较多，请稍后重试。' }
      case 'EVENT_REGISTRATION_NOT_FOUND':
        return { title: '无报名记录', message: '你没有该活动的报名记录。' }
      case 'EVENT_STATE_INVALID':
        return { title: '状态不允许', message: '当前活动状态不允许此操作。' }
      case 'EVENT_CAPACITY_INVALID':
        return { title: '容量无效', message: '容量不能低于已报名人数。' }
      case 'EVENT_TIME_INVALID':
        return {
          title: '时间无效',
          message: '开始时间必须晚于当前时间；结束时间必须晚于开始时间；报名截止不能晚于开始时间。',
        }
      case 'EVENT_NOT_FOUND':
        return { title: '活动不存在', message: '活动不存在或当前不可见。' }
      case 'RESOURCE_VERSION_CONFLICT':
        return { title: '版本冲突', message: '数据已被他人修改，请刷新后重试。' }
    }
    if (error.status === 403) {
      return { title: '权限不足', message: '当前账号没有执行此操作的权限。' }
    }
    if (error.status === 422) {
      return { title: '输入无效', message: error.details[0]?.reason ?? '请检查填写内容。' }
    }
    if (error.status === 429) {
      return { title: '请求过于频繁', message: '请稍后再试。' }
    }
  }
  return { title: fallback, message: '服务暂不可用，请稍后重试。' }
}

// ---------- 活动详情 ----------
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailFailed = ref(false)
const detail = ref<CampusEvent | null>(null)
/** 记录当前详情目标：加载失败重试仍需要它。 */
const detailId = ref('')
const actionError = ref<{ title: string; message: string } | null>(null)
const actionPending = ref(false)
/** 同一次详情会话固定报名幂等键：重试复用，避免重复报名。 */
const registerKey = ref(crypto.randomUUID())

const isOrganizer = computed(() => !!detail.value && detail.value.organizer.user_id === auth.user?.id)
const canManage = computed(() => canWrite.value && (isOrganizer.value || canModerate.value))
const canRegister = computed(
  () =>
    canWrite.value &&
    !!detail.value &&
    detail.value.status === 'published' &&
    detail.value.my_registration_status !== 'registered',
)
const canCancelRegistration = computed(
  () => canWrite.value && !!detail.value && detail.value.my_registration_status === 'registered',
)

async function openDetail(eventId: string) {
  detailId.value = eventId
  detailOpen.value = true
  detailLoading.value = true
  detailFailed.value = false
  actionError.value = null
  registerKey.value = crypto.randomUUID()
  try {
    const response = await callApi(() => getCampusEvent({ path: { event_id: eventId } }))
    detail.value = response.data
  } catch {
    detail.value = null
    detailFailed.value = true
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detailOpen.value = false
  detail.value = null
  actionError.value = null
}

async function reloadDetail() {
  if (!detail.value) {
    return
  }
  const response = await callApi(() => getCampusEvent({ path: { event_id: detail.value!.id } }))
  detail.value = response.data
}

async function register() {
  if (!detail.value || actionPending.value) {
    return
  }
  actionPending.value = true
  actionError.value = null
  try {
    await callApi(() =>
      registerCampusEvent({
        path: { event_id: detail.value!.id },
        headers: { 'Idempotency-Key': registerKey.value },
      }),
    )
    await reloadDetail()
    await load()
  } catch (error) {
    actionError.value = describeEventError(error, '报名失败')
  } finally {
    actionPending.value = false
  }
}

async function cancelRegistration() {
  if (!detail.value || actionPending.value) {
    return
  }
  actionPending.value = true
  actionError.value = null
  try {
    await callApi(() => cancelMyEventRegistration({ path: { event_id: detail.value!.id } }))
    await reloadDetail()
    await load()
  } catch (error) {
    actionError.value = describeEventError(error, '取消报名失败')
  } finally {
    actionPending.value = false
  }
}

// ---------- 创建 / 编辑 ----------
const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const editorPending = ref(false)
const editorError = ref<{ title: string; message: string } | null>(null)
/** 同一次编辑会话固定幂等键：重试复用，避免重复创建。 */
const editorKey = ref(crypto.randomUUID())
const editingId = ref('')
const editorVersion = ref(1)
const editorForm = reactive({
  title: '',
  category: '',
  location: '',
  startsAt: '',
  endsAt: '',
  deadline: '',
  capacity: 50,
  description: '',
})

const startsAtError = computed(() => {
  if (!editorForm.startsAt) return undefined
  return new Date(editorForm.startsAt).getTime() <= Date.now() ? '开始时间必须晚于当前时间' : undefined
})

const endsAtError = computed(() => {
  if (!editorForm.startsAt || !editorForm.endsAt) return undefined
  return new Date(editorForm.endsAt).getTime() <= new Date(editorForm.startsAt).getTime()
    ? '结束时间必须晚于开始时间'
    : undefined
})

const deadlineError = computed(() => {
  if (!editorForm.startsAt || !editorForm.deadline) return undefined
  return new Date(editorForm.deadline).getTime() > new Date(editorForm.startsAt).getTime()
    ? '报名截止不能晚于开始时间'
    : undefined
})

const editorHasLegacyCategory = computed(
  () =>
    editorMode.value === 'edit' &&
    !!editorForm.category &&
    !EVENT_CATEGORY_OPTIONS.some((option) => option.value === editorForm.category),
)

const editorValid = computed(
  () =>
    editorForm.title.trim().length >= 2 &&
    editorForm.category.trim().length >= 1 &&
    editorForm.location.trim().length >= 2 &&
    editorForm.description.trim().length >= 1 &&
    editorForm.startsAt !== '' &&
    editorForm.endsAt !== '' &&
    editorForm.deadline !== '' &&
    !startsAtError.value &&
    !endsAtError.value &&
    !deadlineError.value &&
    editorForm.capacity >= 1 &&
    editorForm.capacity <= 10000,
)

function resetEditorForm() {
  editorForm.title = ''
  editorForm.category = ''
  editorForm.location = ''
  editorForm.startsAt = ''
  editorForm.endsAt = ''
  editorForm.deadline = ''
  editorForm.capacity = 50
  editorForm.description = ''
}

function openCreate() {
  editorMode.value = 'create'
  editingId.value = ''
  editorVersion.value = 1
  editorKey.value = crypto.randomUUID()
  editorError.value = null
  resetEditorForm()
  editorOpen.value = true
}

function openEdit() {
  if (!detail.value) {
    return
  }
  const event = detail.value
  editorMode.value = 'edit'
  editingId.value = event.id
  editorVersion.value = event.version
  editorError.value = null
  editorForm.title = event.title
  editorForm.category = event.category
  editorForm.location = event.location
  editorForm.startsAt = toInputValue(event.starts_at)
  editorForm.endsAt = toInputValue(event.ends_at)
  editorForm.deadline = toInputValue(event.registration_deadline)
  editorForm.capacity = event.capacity
  editorForm.description = event.description_markdown
  editorOpen.value = true
}

async function submitEditor() {
  if (!editorValid.value || editorPending.value) {
    return
  }
  editorPending.value = true
  editorError.value = null
  const body = {
    title: editorForm.title.trim(),
    description_markdown: editorForm.description.trim(),
    category: editorForm.category.trim(),
    location: editorForm.location.trim(),
    starts_at: toIso(editorForm.startsAt),
    ends_at: toIso(editorForm.endsAt),
    registration_deadline: toIso(editorForm.deadline),
    capacity: editorForm.capacity,
  }
  try {
    if (editorMode.value === 'create') {
      await callApi(() =>
        createCampusEvent({ body, headers: { 'Idempotency-Key': editorKey.value } }),
      )
      editorKey.value = crypto.randomUUID()
    } else {
      await callApi(() =>
        updateCampusEvent({ path: { event_id: editingId.value }, body: { ...body, version: editorVersion.value } }),
      )
    }
    editorOpen.value = false
    if (detailOpen.value) {
      await reloadDetail()
    }
    await load()
  } catch (error) {
    editorError.value = describeEventError(error, editorMode.value === 'create' ? '发布失败' : '保存失败')
  } finally {
    editorPending.value = false
  }
}

// ---------- 取消活动 ----------
const cancelOpen = ref(false)
const cancelReason = ref('')
const cancelPending = ref(false)
const cancelError = ref<{ title: string; message: string } | null>(null)
/** 同一次取消会话固定幂等键：重试复用。 */
const cancelKey = ref(crypto.randomUUID())

function openCancel() {
  cancelReason.value = ''
  cancelError.value = null
  cancelKey.value = crypto.randomUUID()
  cancelOpen.value = true
}

async function submitCancel() {
  if (!detail.value || cancelReason.value.trim().length < 2 || cancelPending.value) {
    return
  }
  cancelPending.value = true
  cancelError.value = null
  try {
    await callApi(() =>
      cancelCampusEvent({
        path: { event_id: detail.value!.id },
        body: { reason: cancelReason.value.trim(), version: detail.value!.version },
        headers: { 'Idempotency-Key': cancelKey.value },
      }),
    )
    cancelOpen.value = false
    await reloadDetail()
    await load()
  } catch (error) {
    cancelError.value = describeEventError(error, '取消活动失败')
  } finally {
    cancelPending.value = false
  }
}

// ---------- 报名名单 ----------
const regsOpen = ref(false)
const regs = ref<EventRegistration[]>([])
const regsTotal = ref(0)
const regsPage = ref(1)
const regsLoading = ref(false)
const regsFailed = ref(false)
const REGS_PAGE_SIZE = 10

async function loadRegs() {
  if (!detail.value) {
    return
  }
  regsLoading.value = true
  regsFailed.value = false
  try {
    const response = await callApi(() =>
      listEventRegistrations({
        path: { event_id: detail.value!.id },
        query: { page: regsPage.value, page_size: REGS_PAGE_SIZE },
      }),
    )
    regs.value = response.data.items
    regsTotal.value = response.data.pagination.total
  } catch {
    regsFailed.value = true
  } finally {
    regsLoading.value = false
  }
}

async function openRegs() {
  regsPage.value = 1
  regsOpen.value = true
  await loadRegs()
}

async function changeRegsPage(next: number) {
  regsPage.value = next
  await loadRegs()
}
</script>

<template>
  <div class="events">
    <PageHeader title="校园活动" subtitle="浏览与报名校园活动；发布、修改与取消需要有写权限">
      <UiButton v-if="canWrite" variant="primary" @click="openCreate">发布活动</UiButton>
    </PageHeader>

    <UiCard padding="md">
      <div class="events__filters">
        <UiField label="类别" input-id="event-filter-category">
          <select id="event-filter-category" v-model="filters.category" class="events__input">
            <option value="">全部类别</option>
            <option v-for="option in EVENT_CATEGORY_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </UiField>
        <UiField label="开始时间从" input-id="event-filter-from">
          <input id="event-filter-from" v-model="filters.startsFrom" class="events__input" type="date" />
        </UiField>
        <UiField label="开始时间至" input-id="event-filter-to">
          <input id="event-filter-to" v-model="filters.startsTo" class="events__input" type="date" />
        </UiField>
        <label class="events__check">
          <input v-model="filters.availableOnly" type="checkbox" />
          仅看可报名
        </label>
        <label class="events__check">
          <input v-model="filters.mine" type="checkbox" />
          只看我组织的
        </label>
        <div class="events__filter-actions">
          <UiButton variant="primary" size="sm" @click="applyFilters">筛选</UiButton>
          <UiButton size="sm" @click="resetFilters">重置</UiButton>
        </div>
      </div>
    </UiCard>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="活动列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无活动" description="调整筛选条件，或发布一个新活动" />
    <template v-else>
      <div class="events__list">
        <UiCard v-for="event in events" :key="event.id" class="events__item" padding="md" @click="openDetail(event.id)">
          <div class="events__item-head">
            <StatusBadge :status="event.status" :label="EVENT_STATUS_LABELS[event.status]" />
            <span class="events__category">{{ eventCategoryLabel(event.category) }}</span>
            <span v-if="isFull(event)" class="events__full">名额已满</span>
            <span v-if="event.my_registration_status === 'registered'" class="events__mine">已报名</span>
            <time class="events__time">{{ formatTime(event.starts_at) }}</time>
          </div>
          <p class="events__title">{{ event.title }}</p>
          <p class="events__meta">
            {{ event.location }} · 截止 {{ formatTime(event.registration_deadline) }} · 名额
            {{ event.registered_count }}/{{ event.capacity }}
          </p>
        </UiCard>
      </div>
      <div class="events__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <!-- 活动详情对话框 -->
    <el-dialog v-if="detailOpen" v-model="detailOpen" title="活动详情" width="640px" @close="closeDetail">
      <UiSkeleton v-if="detailLoading" :lines="4" />
      <ErrorState v-else-if="detailFailed" title="活动详情加载失败" @retry="openDetail(detailId)" />
      <template v-else-if="detail">
        <div class="events__detail-head">
          <StatusBadge :status="detail.status" :label="EVENT_STATUS_LABELS[detail.status]" />
          <span class="events__category">{{ eventCategoryLabel(detail.category) }}</span>
          <span v-if="detail.my_registration_status === 'registered'" class="events__mine">已报名</span>
          <span v-else-if="detail.my_registration_status === 'cancelled'" class="events__muted-tag">报名已取消</span>
        </div>
        <h2 class="events__detail-title">{{ detail.title }}</h2>
        <dl class="events__detail-meta">
          <div><dt>活动时间</dt><dd>{{ formatTime(detail.starts_at) }} 至 {{ formatTime(detail.ends_at) }}</dd></div>
          <div><dt>地点</dt><dd>{{ detail.location }}</dd></div>
          <div><dt>报名截止</dt><dd>{{ formatTime(detail.registration_deadline) }}</dd></div>
          <div>
            <dt>名额</dt>
            <dd>
              {{ detail.registered_count }}/{{ detail.capacity }}
              <span v-if="isFull(detail)" class="events__full">已满</span>
            </dd>
          </div>
          <div><dt>组织者</dt><dd>{{ detail.organizer.display_name }}</dd></div>
        </dl>
        <p v-if="detail.status === 'pending_review'" class="events__notice">活动正在审核中，通过后方可报名。</p>
        <p v-else-if="detail.status === 'rejected'" class="events__notice events__notice--error">活动未通过审核。</p>
        <p v-else-if="detail.status === 'cancelled'" class="events__notice events__notice--error">
          活动已取消<span v-if="detail.cancellation_reason">：{{ detail.cancellation_reason }}</span>
        </p>
        <p class="events__description">{{ detail.description_markdown }}</p>

        <p v-if="actionError" class="events__error" role="alert">
          <strong>{{ actionError.title }}</strong>
          <span>{{ actionError.message }}</span>
        </p>

        <div class="events__actions">
          <template v-if="canRegister">
            <UiButton variant="primary" :loading="actionPending" :disabled="isFull(detail)" @click="register">
              {{ isFull(detail) ? '名额已满' : '立即报名' }}
            </UiButton>
          </template>
          <UiButton v-if="canCancelRegistration" :loading="actionPending" @click="cancelRegistration">取消报名</UiButton>
        </div>

        <div v-if="canManage" class="events__manage">
          <p class="events__manage-title">组织者管理</p>
          <div class="events__actions">
            <UiButton size="sm" @click="openEdit">编辑活动</UiButton>
            <UiButton size="sm" @click="openRegs">报名名单</UiButton>
            <UiButton
              v-if="detail.status === 'pending_review' || detail.status === 'published'"
              size="sm"
              variant="danger"
              @click="openCancel"
            >
              取消活动
            </UiButton>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 创建 / 编辑活动对话框 -->
    <el-dialog v-if="editorOpen" v-model="editorOpen" :title="editorMode === 'create' ? '发布活动' : '编辑活动'" width="640px">
      <form class="events__form" @submit.prevent="submitEditor">
        <UiField label="活动标题" input-id="event-form-title" required>
          <input id="event-form-title" v-model="editorForm.title" class="events__input" type="text" maxlength="120" />
        </UiField>
        <div class="events__form-row">
          <UiField label="类别" input-id="event-form-category" required>
            <select id="event-form-category" v-model="editorForm.category" class="events__input">
              <option disabled value="">请选择活动类别</option>
              <option v-if="editorHasLegacyCategory" :value="editorForm.category">
                {{ editorForm.category }}（现有类别）
              </option>
              <option v-for="option in EVENT_CATEGORY_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </UiField>
          <UiField label="名额" input-id="event-form-capacity" required>
            <input id="event-form-capacity" v-model.number="editorForm.capacity" class="events__input" type="number" min="1" max="10000" />
          </UiField>
        </div>
        <UiField label="地点" input-id="event-form-location" required>
          <input id="event-form-location" v-model="editorForm.location" class="events__input" type="text" maxlength="200" />
        </UiField>
        <div class="events__form-row">
          <UiField
            label="开始时间"
            input-id="event-form-starts"
            required
            hint="必须晚于当前时间"
            :error="startsAtError"
          >
            <input id="event-form-starts" v-model="editorForm.startsAt" class="events__input" type="datetime-local" />
          </UiField>
          <UiField label="结束时间" input-id="event-form-ends" required :error="endsAtError">
            <input id="event-form-ends" v-model="editorForm.endsAt" class="events__input" type="datetime-local" />
          </UiField>
        </div>
        <UiField
          label="报名截止"
          input-id="event-form-deadline"
          required
          hint="不能晚于活动开始时间"
          :error="deadlineError"
        >
          <input id="event-form-deadline" v-model="editorForm.deadline" class="events__input" type="datetime-local" />
        </UiField>
        <UiField label="活动描述" input-id="event-form-description" required>
          <textarea
            id="event-form-description"
            v-model="editorForm.description"
            class="events__input"
            rows="5"
            maxlength="5000"
          />
        </UiField>
        <p v-if="editorError" class="events__error" role="alert">
          <strong>{{ editorError.title }}</strong>
          <span>{{ editorError.message }}</span>
        </p>
        <div class="events__actions">
          <UiButton variant="primary" type="submit" :loading="editorPending" :disabled="!editorValid">
            {{ editorMode === 'create' ? '发布活动' : '保存修改' }}
          </UiButton>
          <UiButton @click="editorOpen = false">取消</UiButton>
        </div>
      </form>
    </el-dialog>

    <!-- 取消活动对话框 -->
    <el-dialog v-if="cancelOpen" v-model="cancelOpen" title="取消活动" width="480px">
      <form class="events__form" @submit.prevent="submitCancel">
        <UiField label="取消原因" input-id="event-cancel-reason" required hint="2–500 字，将向报名者展示">
          <textarea id="event-cancel-reason" v-model="cancelReason" class="events__input" rows="3" maxlength="500" />
        </UiField>
        <p v-if="cancelError" class="events__error" role="alert">
          <strong>{{ cancelError.title }}</strong>
          <span>{{ cancelError.message }}</span>
        </p>
        <div class="events__actions">
          <UiButton variant="danger" type="submit" :loading="cancelPending" :disabled="cancelReason.trim().length < 2">
            确认取消活动
          </UiButton>
          <UiButton @click="cancelOpen = false">返回</UiButton>
        </div>
      </form>
    </el-dialog>

    <!-- 报名名单对话框 -->
    <el-dialog v-if="regsOpen" v-model="regsOpen" title="报名名单" width="560px">
      <UiSkeleton v-if="regsLoading" :lines="4" />
      <ErrorState v-else-if="regsFailed" title="名单加载失败" @retry="loadRegs" />
      <EmptyState v-else-if="regs.length === 0" title="暂无报名" />
      <template v-else>
        <ul class="events__regs">
          <li v-for="reg in regs" :key="`${reg.event_id}-${reg.participant.user_id}`" class="events__reg">
            <span class="events__reg-name">{{ reg.participant.display_name }}</span>
            <StatusBadge :status="reg.status" :label="reg.status === 'registered' ? '已报名' : '已取消'" />
            <time class="events__reg-time">{{ formatTime(reg.registered_at) }}</time>
          </li>
        </ul>
        <div class="events__pagination">
          <UiPagination :page="regsPage" :total="regsTotal" :page-size="REGS_PAGE_SIZE" @change="changeRegsPage" />
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.events {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.events__filters {
  display: flex;
  align-items: flex-end;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.events__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  background: var(--cp-surface-card);
}

textarea.events__input {
  padding: var(--cp-space-2) var(--cp-space-3);
  resize: vertical;
}

.events__check {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-2);
  min-height: var(--cp-control-md);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.events__filter-actions {
  display: flex;
  gap: var(--cp-space-2);
  margin-left: auto;
}

.events__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.events__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.events__item:hover {
  border-color: var(--cp-muted);
}

.events__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.events__category {
  font-size: 12px;
  color: var(--cp-muted);
}

.events__full {
  font-size: 12px;
  color: var(--cp-warning);
  font-weight: 500;
}

.events__mine {
  font-size: 12px;
  color: var(--cp-success);
  font-weight: 500;
}

.events__muted-tag {
  font-size: 12px;
  color: var(--cp-muted);
}

.events__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.events__title {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-ink);
  font-size: 15px;
  font-weight: 600;
}

.events__meta {
  margin: var(--cp-space-1) 0 0;
  color: var(--cp-muted);
  font-size: 13px;
}

.events__pagination {
  display: flex;
  justify-content: center;
}

.events__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.events__detail-title {
  margin: var(--cp-space-2) 0;
  font-size: 18px;
  color: var(--cp-ink);
}

.events__detail-meta {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
}

.events__detail-meta div {
  display: flex;
  gap: var(--cp-space-3);
  font-size: 13px;
}

.events__detail-meta dt {
  width: 72px;
  color: var(--cp-muted);
  flex-shrink: 0;
}

.events__detail-meta dd {
  margin: 0;
  color: var(--cp-ink);
}

.events__notice {
  margin: var(--cp-space-3) 0 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
  color: var(--cp-warning);
  font-size: 13px;
}

.events__notice--error {
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
}

.events__description {
  margin: var(--cp-space-3) 0 0;
  color: var(--cp-body);
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}

.events__error {
  margin: var(--cp-space-3) 0 0;
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

.events__actions {
  display: flex;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-4);
  flex-wrap: wrap;
}

.events__manage {
  margin-top: var(--cp-space-4);
  padding-top: var(--cp-space-3);
  border-top: 1px solid var(--cp-hairline-soft);
}

.events__manage-title {
  margin: 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.events__manage .events__actions {
  margin-top: var(--cp-space-2);
}

.events__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.events__form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--cp-space-3);
}

.events__regs {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.events__reg {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  padding: var(--cp-space-2) 0;
  border-bottom: 1px solid var(--cp-hairline-soft);
  font-size: 13px;
}

.events__reg-name {
  color: var(--cp-ink);
  font-weight: 500;
}

.events__reg-time {
  margin-left: auto;
  color: var(--cp-muted-soft);
  font-size: 12px;
}
</style>
