<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import {
  createLostFoundClaim,
  createLostFoundItem,
  deleteLostFoundItem,
  getLostFoundItem,
  listLostFoundItems,
  listLostFoundMatches,
  updateLostFoundItem,
} from '@/api/generated'
import type {
  ContactType,
  LostFoundItem,
  LostFoundItemStatus,
  LostFoundItemType,
  LostFoundMatch,
  LostFoundMatchReason,
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

const ITEM_STATUS_LABELS: Record<LostFoundItemStatus, string> = {
  pending_review: '审核中',
  published: '已发布',
  claiming: '认领中',
  completed: '已完成',
  closed: '已关闭',
  rejected: '已拒绝',
  deleted: '已删除',
}

const ITEM_TYPE_LABELS: Record<LostFoundItemType, string> = {
  lost: '寻物',
  found: '招领',
}

const CONTACT_TYPE_LABELS: Record<ContactType, string> = {
  phone: '电话',
  email: '邮箱',
  wechat: '微信',
  other: '其他',
}

const FACTOR_LABELS: Record<LostFoundMatchReason['factor'], string> = {
  category: '类别',
  location: '地点',
  time: '时间',
  keyword: '关键词',
}

const TYPE_FILTERS: Array<{ value: LostFoundItemType | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'lost', label: '寻物' },
  { value: 'found', label: '招领' },
]

const router = useRouter()
const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('community:write'))
const canModerate = computed(() => auth.hasPermission('community:moderate'))

// ---------- 列表与筛选 ----------
const filters = reactive({ itemType: '' as LostFoundItemType | '', category: '', location: '', mine: false })
const applied = reactive({ itemType: '' as LostFoundItemType | '', category: '', location: '', mine: false })

const {
  items,
  total,
  page,
  pageSize,
  loading,
  failed,
  isEmpty,
  load,
  changePage,
} = useResourceList<LostFoundItem>(async (pageNum, size) => {
  const response = await callApi(() =>
    listLostFoundItems({
      query: {
        page: pageNum,
        page_size: size,
        ...(applied.itemType ? { item_type: applied.itemType } : {}),
        ...(applied.category ? { category: applied.category } : {}),
        ...(applied.location ? { location: applied.location } : {}),
        ...(applied.mine ? { mine: true } : {}),
      },
    }),
  )
  return { items: response.data.items, total: response.data.pagination.total }
})

async function applyFilters() {
  applied.itemType = filters.itemType
  applied.category = filters.category.trim()
  applied.location = filters.location.trim()
  applied.mine = filters.mine
  page.value = 1
  await load()
}

async function changeTypeFilter(value: LostFoundItemType | '') {
  filters.itemType = value
  await applyFilters()
}

async function resetFilters() {
  filters.itemType = ''
  filters.category = ''
  filters.location = ''
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

function describeLostFoundError(error: unknown, fallback: string): { title: string; message: string } {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'LOST_FOUND_ITEM_NOT_FOUND':
        return { title: '记录不存在', message: '该记录不存在或当前不可见。' }
      case 'LOST_FOUND_STATE_INVALID':
        return { title: '状态不允许', message: '当前记录状态不允许此操作。' }
      case 'LOST_FOUND_CLAIM_CONFLICT':
        return { title: '重复认领', message: '你已有一条进行中的认领，请先等待处理结果。' }
      case 'LOST_FOUND_CLAIM_INVALID':
        return {
          title: '无法认领',
          message: '不能认领自己发布的记录，或关联的本人记录类型/状态不符合要求。',
        }
      case 'LOST_FOUND_CLAIM_NOT_FOUND':
        return { title: '认领不存在', message: '认领记录不存在或当前不可见。' }
      case 'LOST_FOUND_CLAIM_STATE_INVALID':
        return { title: '认领状态不允许', message: '当前认领状态不允许此操作。' }
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

// ---------- 详情 ----------
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailFailed = ref(false)
const detail = ref<LostFoundItem | null>(null)
const detailId = ref('')
const actionError = ref<{ title: string; message: string } | null>(null)

const isOwner = computed(() => !!detail.value && detail.value.owner.user_id === auth.user?.id)
const canEditItem = computed(() => canWrite.value && (isOwner.value || canModerate.value))
const canClaim = computed(
  () =>
    canWrite.value &&
    !!detail.value &&
    !isOwner.value &&
    (detail.value.status === 'published' || detail.value.status === 'claiming'),
)

async function openDetail(itemId: string) {
  detailId.value = itemId
  detailOpen.value = true
  detailLoading.value = true
  detailFailed.value = false
  actionError.value = null
  matchesLoaded.value = false
  matches.value = []
  try {
    const response = await callApi(() => getLostFoundItem({ path: { item_id: itemId } }))
    detail.value = response.data
    if (detail.value.owner.user_id === auth.user?.id) {
      await loadMatches()
    }
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
  const response = await callApi(() => getLostFoundItem({ path: { item_id: detail.value!.id } }))
  detail.value = response.data
}

// ---------- 匹配候选（仅记录所有者） ----------
const matches = ref<LostFoundMatch[]>([])
const matchesLoading = ref(false)
const matchesFailed = ref(false)
const matchesLoaded = ref(false)

async function loadMatches() {
  if (!detail.value) {
    return
  }
  matchesLoading.value = true
  matchesFailed.value = false
  try {
    const response = await callApi(() =>
      listLostFoundMatches({ path: { item_id: detail.value!.id }, query: { page: 1, page_size: 20 } }),
    )
    matches.value = response.data.items
    matchesLoaded.value = true
  } catch {
    matchesFailed.value = true
  } finally {
    matchesLoading.value = false
  }
}

function scoreText(score: number): string {
  return `${Math.round(score * 100)}%`
}

// ---------- 创建 / 编辑 ----------
const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const editorPending = ref(false)
const editorError = ref<{ title: string; message: string } | null>(null)
/** 同一次编辑会话固定幂等键：重试复用，避免重复发布。 */
const editorKey = ref(crypto.randomUUID())
const editingId = ref('')
const editorVersion = ref(1)
const editorForm = reactive({
  itemType: 'lost' as LostFoundItemType,
  title: '',
  category: '',
  description: '',
  occurredAt: '',
  location: '',
  contactType: 'phone' as ContactType,
  contactValue: '',
})

const editorValid = computed(() => {
  const base =
    editorForm.title.trim().length >= 2 &&
    editorForm.category.trim().length >= 1 &&
    editorForm.description.trim().length >= 5 &&
    editorForm.occurredAt !== '' &&
    editorForm.location.trim().length >= 2
  if (editorMode.value === 'create') {
    return base && editorForm.contactValue.trim().length >= 3
  }
  // 编辑：联系方式留空表示不修改；填写则必须 ≥3 字（contact_type 随之提交）
  return base && (editorForm.contactValue.trim() === '' || editorForm.contactValue.trim().length >= 3)
})

function resetEditorForm() {
  editorForm.itemType = 'lost'
  editorForm.title = ''
  editorForm.category = ''
  editorForm.description = ''
  editorForm.occurredAt = ''
  editorForm.location = ''
  editorForm.contactType = 'phone'
  editorForm.contactValue = ''
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
  const item = detail.value
  editorMode.value = 'edit'
  editingId.value = item.id
  editorVersion.value = item.version
  editorError.value = null
  editorForm.itemType = item.item_type
  editorForm.title = item.title
  editorForm.category = item.category
  editorForm.description = item.description
  editorForm.occurredAt = toInputValue(item.occurred_at)
  editorForm.location = item.location
  editorForm.contactType = item.contact_type
  editorForm.contactValue = ''
  editorOpen.value = true
}

async function submitEditor() {
  if (!editorValid.value || editorPending.value) {
    return
  }
  editorPending.value = true
  editorError.value = null
  try {
    if (editorMode.value === 'create') {
      await callApi(() =>
        createLostFoundItem({
          body: {
            item_type: editorForm.itemType,
            title: editorForm.title.trim(),
            category: editorForm.category.trim(),
            description: editorForm.description.trim(),
            occurred_at: toIso(editorForm.occurredAt),
            location: editorForm.location.trim(),
            contact_type: editorForm.contactType,
            contact_value: editorForm.contactValue.trim(),
          },
          headers: { 'Idempotency-Key': editorKey.value },
        }),
      )
      editorKey.value = crypto.randomUUID()
    } else {
      const contact = editorForm.contactValue.trim()
      await callApi(() =>
        updateLostFoundItem({
          path: { item_id: editingId.value },
          body: {
            title: editorForm.title.trim(),
            category: editorForm.category.trim(),
            description: editorForm.description.trim(),
            occurred_at: toIso(editorForm.occurredAt),
            location: editorForm.location.trim(),
            ...(contact ? { contact_type: editorForm.contactType, contact_value: contact } : {}),
            version: editorVersion.value,
          },
        }),
      )
    }
    editorOpen.value = false
    if (detailOpen.value && detail.value) {
      await reloadDetail()
    }
    await load()
  } catch (error) {
    editorError.value = describeLostFoundError(error, editorMode.value === 'create' ? '发布失败' : '保存失败')
  } finally {
    editorPending.value = false
  }
}

// ---------- 删除 ----------
const deleteOpen = ref(false)
const deletePending = ref(false)
const deleteError = ref<{ title: string; message: string } | null>(null)

function openDelete() {
  deleteError.value = null
  deleteOpen.value = true
}

async function submitDelete() {
  if (!detail.value || deletePending.value) {
    return
  }
  deletePending.value = true
  deleteError.value = null
  try {
    await callApi(() => deleteLostFoundItem({ path: { item_id: detail.value!.id } }))
    deleteOpen.value = false
    closeDetail()
    await load()
  } catch (error) {
    deleteError.value = describeLostFoundError(error, '删除失败')
  } finally {
    deletePending.value = false
  }
}

// ---------- 发起认领 ----------
const claimOpen = ref(false)
const claimPending = ref(false)
const claimError = ref<{ title: string; message: string } | null>(null)
const claimEvidence = ref('')
const claimItemId = ref('')
const myItems = ref<LostFoundItem[]>([])
/** 同一次认领会话固定幂等键：重试复用，避免重复认领。 */
const claimKey = ref(crypto.randomUUID())

const reverseType = computed<LostFoundItemType>(() => (detail.value?.item_type === 'lost' ? 'found' : 'lost'))

async function openClaim() {
  claimEvidence.value = ''
  claimItemId.value = ''
  claimError.value = null
  claimKey.value = crypto.randomUUID()
  claimOpen.value = true
  myItems.value = []
  try {
    const response = await callApi(() =>
      listLostFoundItems({ query: { page: 1, page_size: 50, mine: true, item_type: reverseType.value } }),
    )
    myItems.value = response.data.items.filter(
      (item) => item.status === 'published' || item.status === 'claiming',
    )
  } catch {
    myItems.value = []
  }
}

async function submitClaim() {
  if (!detail.value || claimEvidence.value.trim().length < 5 || claimPending.value) {
    return
  }
  claimPending.value = true
  claimError.value = null
  try {
    await callApi(() =>
      createLostFoundClaim({
        path: { item_id: detail.value!.id },
        body: {
          evidence: claimEvidence.value.trim(),
          claimant_item_id: claimItemId.value || null,
        },
        headers: { 'Idempotency-Key': claimKey.value },
      }),
    )
    claimOpen.value = false
    await reloadDetail()
    await load()
  } catch (error) {
    claimError.value = describeLostFoundError(error, '认领失败')
  } finally {
    claimPending.value = false
  }
}
</script>

<template>
  <div class="lf">
    <PageHeader title="失物招领" subtitle="发布寻物/招领信息；联系方式加密保存，仅展示脱敏提示">
      <UiButton @click="router.push({ name: 'lost-found-claims' })">我的认领</UiButton>
      <UiButton v-if="canWrite" variant="primary" @click="openCreate">发布信息</UiButton>
    </PageHeader>

    <UiCard padding="md">
      <div class="lf__filters">
        <div class="lf__types" role="tablist">
          <button
            v-for="filter in TYPE_FILTERS"
            :key="filter.label"
            type="button"
            class="lf__type"
            :class="{ 'lf__type--active': filters.itemType === filter.value }"
            @click="changeTypeFilter(filter.value)"
          >
            {{ filter.label }}
          </button>
        </div>
        <UiField label="类别" input-id="lf-filter-category">
          <input
            id="lf-filter-category"
            v-model="filters.category"
            class="lf__input"
            type="text"
            placeholder="如：证件、钥匙、电子产品"
          />
        </UiField>
        <UiField label="地点" input-id="lf-filter-location">
          <input
            id="lf-filter-location"
            v-model="filters.location"
            class="lf__input"
            type="text"
            placeholder="如：图书馆、一食堂"
          />
        </UiField>
        <label class="lf__check">
          <input v-model="filters.mine" type="checkbox" />
          只看我发布的
        </label>
        <div class="lf__filter-actions">
          <UiButton variant="primary" size="sm" @click="applyFilters">筛选</UiButton>
          <UiButton size="sm" @click="resetFilters">重置</UiButton>
        </div>
      </div>
    </UiCard>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无记录" description="调整筛选条件，或发布一条寻物/招领信息" />
    <template v-else>
      <div class="lf__list">
        <UiCard v-for="item in items" :key="item.id" class="lf__item" padding="md" @click="openDetail(item.id)">
          <div class="lf__item-head">
            <span class="lf__badge" :class="`lf__badge--${item.item_type}`">{{ ITEM_TYPE_LABELS[item.item_type] }}</span>
            <StatusBadge :status="item.status" :label="ITEM_STATUS_LABELS[item.status]" />
            <span class="lf__category">{{ item.category }}</span>
            <time class="lf__time">{{ formatTime(item.occurred_at) }}</time>
          </div>
          <p class="lf__title">{{ item.title }}</p>
          <p class="lf__meta">{{ item.location }} · 联系提示：{{ item.contact_hint }}</p>
        </UiCard>
      </div>
      <div class="lf__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <!-- 记录详情对话框 -->
    <el-dialog v-if="detailOpen" v-model="detailOpen" title="记录详情" width="640px" @close="closeDetail">
      <UiSkeleton v-if="detailLoading" :lines="4" />
      <ErrorState v-else-if="detailFailed" title="详情加载失败" @retry="openDetail(detailId)" />
      <template v-else-if="detail">
        <div class="lf__detail-head">
          <span class="lf__badge" :class="`lf__badge--${detail.item_type}`">{{ ITEM_TYPE_LABELS[detail.item_type] }}</span>
          <StatusBadge :status="detail.status" :label="ITEM_STATUS_LABELS[detail.status]" />
          <span class="lf__category">{{ detail.category }}</span>
        </div>
        <h2 class="lf__detail-title">{{ detail.title }}</h2>
        <dl class="lf__detail-meta">
          <div>
            <dt>{{ detail.item_type === 'lost' ? '丢失时间' : '拾到时间' }}</dt>
            <dd>{{ formatTime(detail.occurred_at) }}</dd>
          </div>
          <div><dt>地点</dt><dd>{{ detail.location }}</dd></div>
          <div><dt>发布者</dt><dd>{{ detail.owner.display_name }}</dd></div>
          <div>
            <dt>联系方式</dt>
            <dd>{{ CONTACT_TYPE_LABELS[detail.contact_type] }} · {{ detail.contact_hint }}（脱敏）</dd>
          </div>
        </dl>
        <p class="lf__hint">完整联系方式加密保存，认领验证通过后双方才可见。</p>
        <p v-if="detail.status === 'pending_review'" class="lf__notice">记录正在审核中。</p>
        <p v-else-if="detail.status === 'completed'" class="lf__notice lf__notice--success">交接已完成。</p>
        <p class="lf__description">{{ detail.description }}</p>

        <p v-if="actionError" class="lf__error" role="alert">
          <strong>{{ actionError.title }}</strong>
          <span>{{ actionError.message }}</span>
        </p>

        <div class="lf__actions">
          <UiButton v-if="canClaim" variant="primary" @click="openClaim">发起认领</UiButton>
          <template v-if="canEditItem">
            <UiButton size="sm" @click="openEdit">编辑</UiButton>
            <UiButton
              v-if="detail.status !== 'completed' && detail.status !== 'deleted'"
              size="sm"
              variant="danger"
              @click="openDelete"
            >
              删除
            </UiButton>
          </template>
        </div>

        <!-- 可能的匹配（仅所有者可见） -->
        <div v-if="isOwner" class="lf__matches">
          <p class="lf__section-title">可能的匹配</p>
          <UiSkeleton v-if="matchesLoading" :lines="3" />
          <ErrorState v-else-if="matchesFailed" title="匹配加载失败" @retry="loadMatches" />
          <p v-else-if="matchesLoaded && matches.length === 0" class="lf__muted">暂无可解释的候选匹配。</p>
          <ul v-else class="lf__match-list">
            <li v-for="match in matches" :key="match.id" class="lf__match">
              <div class="lf__match-head">
                <span class="lf__badge" :class="`lf__badge--${match.candidate.item_type}`">
                  {{ ITEM_TYPE_LABELS[match.candidate.item_type] }}
                </span>
                <span class="lf__match-title">{{ match.candidate.title }}</span>
                <span class="lf__match-score">匹配度 {{ scoreText(match.score) }}</span>
              </div>
              <p class="lf__match-meta">
                {{ match.candidate.location }} · {{ formatTime(match.candidate.occurred_at) }}
              </p>
              <ul class="lf__reasons">
                <li v-for="reason in match.reasons" :key="reason.factor">
                  <span class="lf__reason-factor">{{ FACTOR_LABELS[reason.factor] }} {{ scoreText(reason.score) }}</span>
                  <span class="lf__reason-text">{{ reason.explanation }}</span>
                </li>
              </ul>
            </li>
          </ul>
        </div>
      </template>
    </el-dialog>

    <!-- 发布 / 编辑对话框 -->
    <el-dialog v-if="editorOpen" v-model="editorOpen" :title="editorMode === 'create' ? '发布信息' : '编辑记录'" width="640px">
      <form class="lf__form" @submit.prevent="submitEditor">
        <UiField v-if="editorMode === 'create'" label="信息类型" input-id="lf-form-type" required>
          <div class="lf__types" role="radiogroup">
            <label
              v-for="option in TYPE_FILTERS.slice(1)"
              :key="option.value"
              class="lf__type-option"
              :class="{ 'lf__type-option--active': editorForm.itemType === option.value }"
            >
              <input v-model="editorForm.itemType" type="radio" name="lf-type" :value="option.value" class="lf__sr" />
              {{ option.label === '寻物' ? '寻物（我丢失了物品）' : '招领（我拾到物品）' }}
            </label>
          </div>
        </UiField>
        <UiField :label="editorForm.itemType === 'lost' ? '物品名称' : '拾到物品'" input-id="lf-form-title" required>
          <input id="lf-form-title" v-model="editorForm.title" class="lf__input" type="text" maxlength="120" />
        </UiField>
        <div class="lf__form-row">
          <UiField label="类别" input-id="lf-form-category" required>
            <input id="lf-form-category" v-model="editorForm.category" class="lf__input" type="text" maxlength="50" />
          </UiField>
          <UiField :label="editorForm.itemType === 'lost' ? '丢失时间' : '拾到时间'" input-id="lf-form-occurred" required>
            <input id="lf-form-occurred" v-model="editorForm.occurredAt" class="lf__input" type="datetime-local" />
          </UiField>
        </div>
        <UiField label="地点" input-id="lf-form-location" required>
          <input id="lf-form-location" v-model="editorForm.location" class="lf__input" type="text" maxlength="200" />
        </UiField>
        <UiField label="详细描述" input-id="lf-form-description" required hint="5–2000 字；请勿填写完整联系方式">
          <textarea id="lf-form-description" v-model="editorForm.description" class="lf__input" rows="4" maxlength="2000" />
        </UiField>
        <div class="lf__form-row">
          <UiField label="联系方式类型" input-id="lf-form-contact-type" :required="editorMode === 'create'">
            <select id="lf-form-contact-type" v-model="editorForm.contactType" class="lf__input">
              <option v-for="(label, value) in CONTACT_TYPE_LABELS" :key="value" :value="value">{{ label }}</option>
            </select>
          </UiField>
          <UiField
            label="联系方式"
            input-id="lf-form-contact-value"
            :required="editorMode === 'create'"
            :hint="editorMode === 'edit' ? '留空则不修改；填写则加密更新' : '加密保存，仅向认领验证通过的对方展示'"
          >
            <input
              id="lf-form-contact-value"
              v-model="editorForm.contactValue"
              class="lf__input"
              type="text"
              maxlength="200"
              autocomplete="off"
            />
          </UiField>
        </div>
        <p v-if="editorError" class="lf__error" role="alert">
          <strong>{{ editorError.title }}</strong>
          <span>{{ editorError.message }}</span>
        </p>
        <div class="lf__actions">
          <UiButton variant="primary" type="submit" :loading="editorPending" :disabled="!editorValid">
            {{ editorMode === 'create' ? '发布' : '保存修改' }}
          </UiButton>
          <UiButton @click="editorOpen = false">取消</UiButton>
        </div>
      </form>
    </el-dialog>

    <!-- 删除确认对话框 -->
    <el-dialog v-if="deleteOpen" v-model="deleteOpen" title="删除记录" width="440px">
      <p class="lf__muted">删除后记录与候选匹配关系将失效，进行中的认领会被驳回。该操作不可撤销。</p>
      <p v-if="deleteError" class="lf__error" role="alert">
        <strong>{{ deleteError.title }}</strong>
        <span>{{ deleteError.message }}</span>
      </p>
      <div class="lf__actions">
        <UiButton variant="danger" :loading="deletePending" @click="submitDelete">确认删除</UiButton>
        <UiButton @click="deleteOpen = false">返回</UiButton>
      </div>
    </el-dialog>

    <!-- 发起认领对话框 -->
    <el-dialog v-if="claimOpen" v-model="claimOpen" title="发起认领" width="560px">
      <form class="lf__form" @submit.prevent="submitClaim">
        <UiField
          label="验证说明"
          input-id="lf-claim-evidence"
          required
          hint="5–1000 字；描述只有物主/拾到者才知道的关键特征，将加密提交给记录发布者核验"
        >
          <textarea id="lf-claim-evidence" v-model="claimEvidence" class="lf__input" rows="4" maxlength="1000" />
        </UiField>
        <UiField
          v-if="myItems.length > 0"
          label="关联我的反向记录（可选）"
          input-id="lf-claim-item"
          :hint="`你发布的${ITEM_TYPE_LABELS[reverseType]}记录，用于验证通过后交换联系方式`"
        >
          <select id="lf-claim-item" v-model="claimItemId" class="lf__input">
            <option value="">不关联</option>
            <option v-for="item in myItems" :key="item.id" :value="item.id">{{ item.title }}</option>
          </select>
        </UiField>
        <p v-if="claimError" class="lf__error" role="alert">
          <strong>{{ claimError.title }}</strong>
          <span>{{ claimError.message }}</span>
        </p>
        <div class="lf__actions">
          <UiButton variant="primary" type="submit" :loading="claimPending" :disabled="claimEvidence.trim().length < 5">
            提交认领
          </UiButton>
          <UiButton @click="claimOpen = false">取消</UiButton>
        </div>
      </form>
    </el-dialog>
  </div>
</template>

<style scoped>
.lf {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.lf__filters {
  display: flex;
  align-items: flex-end;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.lf__types {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.lf__type {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.lf__type--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.lf__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  background: var(--cp-surface-card);
}

textarea.lf__input {
  padding: var(--cp-space-2) var(--cp-space-3);
  resize: vertical;
}

.lf__check {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-2);
  min-height: var(--cp-control-md);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.lf__filter-actions {
  display: flex;
  gap: var(--cp-space-2);
  margin-left: auto;
}

.lf__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.lf__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.lf__item:hover {
  border-color: var(--cp-muted);
}

.lf__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.lf__badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: var(--cp-radius-pill);
  font-size: 12px;
  font-weight: 500;
  line-height: 20px;
}

.lf__badge--lost {
  color: var(--cp-error);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  background: color-mix(in srgb, var(--cp-error) 7%, white);
}

.lf__badge--found {
  color: var(--cp-success);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
}

.lf__category {
  font-size: 12px;
  color: var(--cp-muted);
}

.lf__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.lf__title {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-ink);
  font-size: 15px;
  font-weight: 600;
}

.lf__meta {
  margin: var(--cp-space-1) 0 0;
  color: var(--cp-muted);
  font-size: 13px;
}

.lf__pagination {
  display: flex;
  justify-content: center;
}

.lf__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.lf__detail-title {
  margin: var(--cp-space-2) 0;
  font-size: 18px;
  color: var(--cp-ink);
}

.lf__detail-meta {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
}

.lf__detail-meta div {
  display: flex;
  gap: var(--cp-space-3);
  font-size: 13px;
}

.lf__detail-meta dt {
  width: 72px;
  color: var(--cp-muted);
  flex-shrink: 0;
}

.lf__detail-meta dd {
  margin: 0;
  color: var(--cp-ink);
}

.lf__hint {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.lf__notice {
  margin: var(--cp-space-3) 0 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
  color: var(--cp-warning);
  font-size: 13px;
}

.lf__notice--success {
  background: color-mix(in srgb, var(--cp-success) 8%, white);
  color: var(--cp-success);
}

.lf__description {
  margin: var(--cp-space-3) 0 0;
  color: var(--cp-body);
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}

.lf__error {
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

.lf__actions {
  display: flex;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-4);
  flex-wrap: wrap;
}

.lf__matches {
  margin-top: var(--cp-space-4);
  padding-top: var(--cp-space-3);
  border-top: 1px solid var(--cp-hairline-soft);
}

.lf__section-title {
  margin: 0 0 var(--cp-space-2);
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink);
}

.lf__muted {
  margin: 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.lf__match-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.lf__match {
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
}

.lf__match-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.lf__match-title {
  color: var(--cp-ink);
  font-size: 14px;
  font-weight: 500;
}

.lf__match-score {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-info);
  font-weight: 500;
}

.lf__match-meta {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.lf__reasons {
  margin: var(--cp-space-2) 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.lf__reasons li {
  display: flex;
  gap: var(--cp-space-2);
  font-size: 12px;
}

.lf__reason-factor {
  flex-shrink: 0;
  color: var(--cp-info);
  font-weight: 500;
}

.lf__reason-text {
  color: var(--cp-muted);
}

.lf__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.lf__form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--cp-space-3);
}

.lf__type-option {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.lf__type-option--active {
  border-color: var(--cp-primary);
  color: var(--cp-primary);
  background: color-mix(in srgb, var(--cp-primary) 6%, white);
}

.lf__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
