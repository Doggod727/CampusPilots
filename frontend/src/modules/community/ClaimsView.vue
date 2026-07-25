<script setup lang="ts">
import { computed, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import {
  confirmLostFoundClaimCompletion,
  decideLostFoundClaim,
  getLostFoundClaim,
  getLostFoundClaimContact,
  listMyLostFoundClaims,
} from '@/api/generated'
import type {
  ContactType,
  LostFoundClaim,
  LostFoundClaimStatus,
  LostFoundContactData,
  LostFoundItemType,
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

const CLAIM_STATUS_LABELS: Record<LostFoundClaimStatus, string> = {
  pending: '待处理',
  verified: '已批准',
  rejected: '已拒绝',
  cancelled: '已取消',
  completed: '已完成',
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

const ROLE_FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'claimant', label: '我发起的' },
  { value: 'owner', label: '待我处理的' },
] as const

const STATUS_FILTERS: Array<{ value: LostFoundClaimStatus | ''; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'verified', label: '已批准' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'completed', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]

type ClaimRole = (typeof ROLE_FILTERS)[number]['value']

const router = useRouter()
const auth = useAuthStore()

// ---------- 列表与筛选 ----------
const filters = reactive({ role: 'all' as ClaimRole, status: '' as LostFoundClaimStatus | '' })
const applied = reactive({ role: 'all' as ClaimRole, status: '' as LostFoundClaimStatus | '' })

const {
  items: claims,
  total,
  page,
  pageSize,
  loading,
  failed,
  isEmpty,
  load,
  changePage,
} = useResourceList<LostFoundClaim>(async (pageNum, size) => {
  const response = await callApi(() =>
    listMyLostFoundClaims({
      query: {
        page: pageNum,
        page_size: size,
        role: applied.role,
        ...(applied.status ? { status: applied.status } : {}),
      },
    }),
  )
  return { items: response.data.items, total: response.data.pagination.total }
})

async function applyFilters() {
  applied.role = filters.role
  applied.status = filters.status
  page.value = 1
  await load()
}

async function changeRole(value: ClaimRole) {
  filters.role = value
  await applyFilters()
}

async function changeStatus(value: LostFoundClaimStatus | '') {
  filters.status = value
  await applyFilters()
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function myRole(claim: LostFoundClaim): 'claimant' | 'owner' | null {
  const me = auth.user?.id
  if (claim.claimant.user_id === me) {
    return 'claimant'
  }
  if (claim.target_item.owner.user_id === me) {
    return 'owner'
  }
  return null
}

function describeClaimError(error: unknown, fallback: string): { title: string; message: string } {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'LOST_FOUND_CLAIM_NOT_FOUND':
        return { title: '认领不存在', message: '认领记录不存在或当前不可见。' }
      case 'LOST_FOUND_ITEM_NOT_FOUND':
        return { title: '记录不存在', message: '关联的失物招领记录不存在或当前不可见。' }
      case 'LOST_FOUND_CLAIM_STATE_INVALID':
        return { title: '状态不允许', message: '当前认领状态不允许此操作。' }
      case 'LOST_FOUND_CLAIM_CONFLICT':
        return { title: '重复认领', message: '已存在进行中的认领。' }
      case 'LOST_FOUND_CLAIM_INVALID':
        return { title: '操作无效', message: '当前记录存在进行中的认领或输入不符合要求。' }
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

// ---------- 认领详情 ----------
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailFailed = ref(false)
const detail = ref<LostFoundClaim | null>(null)
const detailId = ref('')
const actionError = ref<{ title: string; message: string } | null>(null)
const actionPending = ref(false)
/** 同一次详情会话固定幂等键：批准/完成确认重试复用。 */
const decideKey = ref(crypto.randomUUID())
const completeKey = ref(crypto.randomUUID())

const isClaimant = computed(() => !!detail.value && myRole(detail.value) === 'claimant')
const isTargetOwner = computed(() => !!detail.value && myRole(detail.value) === 'owner')
const canDecide = computed(() => isTargetOwner.value && detail.value?.status === 'pending')
const canViewContact = computed(
  () =>
    (isClaimant.value || isTargetOwner.value) &&
    (detail.value?.status === 'verified' || detail.value?.status === 'completed'),
)
const myConfirmed = computed(() => {
  if (!detail.value) {
    return false
  }
  if (isClaimant.value) {
    return detail.value.claimant_confirmed
  }
  if (isTargetOwner.value) {
    return detail.value.owner_confirmed
  }
  return false
})
const canComplete = computed(
  () => (isClaimant.value || isTargetOwner.value) && detail.value?.status === 'verified' && !myConfirmed.value,
)

async function openDetail(claimId: string) {
  detailId.value = claimId
  detailOpen.value = true
  detailLoading.value = true
  detailFailed.value = false
  actionError.value = null
  decideKey.value = crypto.randomUUID()
  completeKey.value = crypto.randomUUID()
  rejectOpen.value = false
  try {
    const response = await callApi(() => getLostFoundClaim({ path: { claim_id: claimId } }))
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
  clearContact()
}

async function reloadDetail() {
  if (!detail.value) {
    return
  }
  const response = await callApi(() => getLostFoundClaim({ path: { claim_id: detail.value!.id } }))
  detail.value = response.data
}

// ---------- 批准 / 拒绝 ----------
const rejectOpen = ref(false)
const rejectReason = ref('')

async function submitDecision(decision: 'verified' | 'rejected') {
  if (!detail.value || actionPending.value) {
    return
  }
  const reason = decision === 'rejected' ? rejectReason.value.trim() : null
  if (decision === 'rejected' && (reason === null || reason.length < 2)) {
    return
  }
  actionPending.value = true
  actionError.value = null
  try {
    await callApi(() =>
      decideLostFoundClaim({
        path: { claim_id: detail.value!.id },
        body: { decision, reason, version: detail.value!.version },
        headers: { 'Idempotency-Key': decideKey.value },
      }),
    )
    rejectOpen.value = false
    rejectReason.value = ''
    await reloadDetail()
    await load()
  } catch (error) {
    actionError.value = describeClaimError(error, decision === 'verified' ? '批准失败' : '拒绝失败')
  } finally {
    actionPending.value = false
  }
}

// ---------- 联系方式（仅内存，关闭即清空） ----------
const contactOpen = ref(false)
const contactLoading = ref(false)
const contactError = ref<{ title: string; message: string } | null>(null)
const contact = ref<LostFoundContactData | null>(null)

async function openContact() {
  if (!detail.value) {
    return
  }
  contactOpen.value = true
  contactLoading.value = true
  contactError.value = null
  contact.value = null
  try {
    const response = await callApi(() => getLostFoundClaimContact({ path: { claim_id: detail.value!.id } }))
    contact.value = response.data
  } catch (error) {
    contactError.value = describeClaimError(error, '获取联系方式失败')
  } finally {
    contactLoading.value = false
  }
}

function clearContact() {
  contact.value = null
  contactError.value = null
  contactLoading.value = false
  contactOpen.value = false
}

onUnmounted(clearContact)

// ---------- 完成确认 ----------
async function submitComplete() {
  if (!detail.value || actionPending.value) {
    return
  }
  actionPending.value = true
  actionError.value = null
  try {
    await callApi(() =>
      confirmLostFoundClaimCompletion({
        path: { claim_id: detail.value!.id },
        body: { version: detail.value!.version },
        headers: { 'Idempotency-Key': completeKey.value },
      }),
    )
    await reloadDetail()
    await load()
  } catch (error) {
    actionError.value = describeClaimError(error, '确认完成失败')
  } finally {
    actionPending.value = false
  }
}
</script>

<template>
  <div class="claims">
    <PageHeader title="我的认领" subtitle="我发起的认领与待我核验的认领请求">
      <UiButton @click="router.push({ name: 'lost-found' })">返回失物招领</UiButton>
    </PageHeader>

    <UiCard padding="md">
      <div class="claims__filters">
        <div class="claims__chips" role="tablist" aria-label="角色筛选">
          <button
            v-for="filter in ROLE_FILTERS"
            :key="filter.value"
            type="button"
            class="claims__chip"
            :class="{ 'claims__chip--active': filters.role === filter.value }"
            @click="changeRole(filter.value)"
          >
            {{ filter.label }}
          </button>
        </div>
        <div class="claims__chips" role="tablist" aria-label="状态筛选">
          <button
            v-for="filter in STATUS_FILTERS"
            :key="filter.label"
            type="button"
            class="claims__chip"
            :class="{ 'claims__chip--active': filters.status === filter.value }"
            @click="changeStatus(filter.value)"
          >
            {{ filter.label }}
          </button>
        </div>
      </div>
    </UiCard>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="认领列表加载失败" @retry="load" />
    <EmptyState v-else-if="isEmpty" title="暂无认领记录" description="在失物招领详情里可以发起认领" />
    <template v-else>
      <div class="claims__list">
        <UiCard v-for="claim in claims" :key="claim.id" class="claims__item" padding="md" @click="openDetail(claim.id)">
          <div class="claims__item-head">
            <span class="claims__badge" :class="`claims__badge--${claim.target_item.item_type}`">
              {{ ITEM_TYPE_LABELS[claim.target_item.item_type] }}
            </span>
            <StatusBadge :status="claim.status" :label="CLAIM_STATUS_LABELS[claim.status]" />
            <span class="claims__role">{{ myRole(claim) === 'owner' ? '待我核验' : '我发起的' }}</span>
            <time class="claims__time">{{ formatTime(claim.created_at) }}</time>
          </div>
          <p class="claims__title">{{ claim.target_item.title }}</p>
          <p class="claims__meta">
            认领人：{{ claim.claimant.display_name }} · 认领人{{ claim.claimant_confirmed ? '已确认' : '未确认' }} ·
            物品所有者{{ claim.owner_confirmed ? '已确认' : '未确认' }}
          </p>
        </UiCard>
      </div>
      <div class="claims__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <!-- 认领详情对话框 -->
    <el-dialog v-if="detailOpen" v-model="detailOpen" title="认领详情" width="640px" @close="closeDetail">
      <UiSkeleton v-if="detailLoading" :lines="4" />
      <ErrorState v-else-if="detailFailed" title="认领详情加载失败" @retry="openDetail(detailId)" />
      <template v-else-if="detail">
        <div class="claims__detail-head">
          <span class="claims__badge" :class="`claims__badge--${detail.target_item.item_type}`">
            {{ ITEM_TYPE_LABELS[detail.target_item.item_type] }}
          </span>
          <StatusBadge :status="detail.status" :label="CLAIM_STATUS_LABELS[detail.status]" />
        </div>
        <h2 class="claims__detail-title">{{ detail.target_item.title }}</h2>
        <dl class="claims__detail-meta">
          <div><dt>目标记录</dt><dd>{{ detail.target_item.location }} · {{ formatTime(detail.target_item.occurred_at) }}</dd></div>
          <div><dt>认领人</dt><dd>{{ detail.claimant.display_name }}</dd></div>
          <div><dt>提交时间</dt><dd>{{ formatTime(detail.created_at) }}</dd></div>
          <div>
            <dt>联系提示</dt>
            <dd>{{ detail.target_item.contact_hint }}（脱敏）</dd>
          </div>
        </dl>

        <div class="claims__evidence">
          <p class="claims__section-title">验证说明</p>
          <p class="claims__evidence-text">{{ detail.evidence }}</p>
        </div>

        <p v-if="detail.status === 'rejected' && detail.decision_reason" class="claims__notice claims__notice--error">
          已拒绝：{{ detail.decision_reason }}
        </p>
        <p v-else-if="detail.status === 'verified'" class="claims__notice claims__notice--success">
          认领已批准，请尽快线下交接并双方确认完成。
        </p>
        <p v-else-if="detail.status === 'completed'" class="claims__notice claims__notice--success">
          交接已完成<span v-if="detail.completed_at">：{{ formatTime(detail.completed_at) }}</span>
        </p>

        <div class="claims__progress">
          <span :class="detail.claimant_confirmed ? 'claims__done' : 'claims__todo'">
            认领人{{ detail.claimant_confirmed ? '已确认完成' : '未确认完成' }}
          </span>
          <span :class="detail.owner_confirmed ? 'claims__done' : 'claims__todo'">
            物品所有者{{ detail.owner_confirmed ? '已确认完成' : '未确认完成' }}
          </span>
        </div>

        <p v-if="actionError" class="claims__error" role="alert">
          <strong>{{ actionError.title }}</strong>
          <span>{{ actionError.message }}</span>
        </p>

        <div class="claims__actions">
          <UiButton v-if="canViewContact" size="sm" @click="openContact">查看联系方式</UiButton>
          <UiButton v-if="canComplete" variant="primary" :loading="actionPending" @click="submitComplete">
            确认完成交接
          </UiButton>
          <template v-if="canDecide">
            <UiButton variant="primary" :loading="actionPending" @click="submitDecision('verified')">批准认领</UiButton>
            <UiButton variant="danger" :disabled="actionPending" @click="rejectOpen = !rejectOpen">拒绝</UiButton>
          </template>
        </div>

        <form v-if="canDecide && rejectOpen" class="claims__reject" @submit.prevent="submitDecision('rejected')">
          <UiField label="拒绝原因" input-id="claim-reject-reason" required hint="2–500 字，将向认领人展示">
            <textarea id="claim-reject-reason" v-model="rejectReason" class="claims__input" rows="3" maxlength="500" />
          </UiField>
          <div class="claims__actions">
            <UiButton variant="danger" type="submit" :loading="actionPending" :disabled="rejectReason.trim().length < 2">
              确认拒绝
            </UiButton>
          </div>
        </form>
      </template>
    </el-dialog>

    <!-- 联系方式对话框：内容仅存内存，关闭即清空 -->
    <el-dialog v-if="contactOpen" v-model="contactOpen" title="双方联系方式" width="480px" @close="clearContact">
      <UiSkeleton v-if="contactLoading" :lines="3" />
      <p v-else-if="contactError" class="claims__error" role="alert">
        <strong>{{ contactError.title }}</strong>
        <span>{{ contactError.message }}</span>
      </p>
      <template v-else-if="contact">
        <div class="claims__contact">
          <p class="claims__contact-label">对方（{{ contact.counterpart.user.display_name }}）</p>
          <p class="claims__contact-value">
            {{ CONTACT_TYPE_LABELS[contact.counterpart.contact_type] }}：{{ contact.counterpart.contact_value }}
          </p>
        </div>
        <div class="claims__contact">
          <p class="claims__contact-label">我（{{ contact.requester.user.display_name }}）</p>
          <p class="claims__contact-value">
            {{ CONTACT_TYPE_LABELS[contact.requester.contact_type] }}：{{ contact.requester.contact_value }}
          </p>
        </div>
        <p class="claims__muted">联系方式仅本次展示，关闭对话框后立即清除，不会保存。</p>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.claims {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.claims__filters {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.claims__chips {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.claims__chip {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.claims__chip--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.claims__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.claims__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.claims__item:hover {
  border-color: var(--cp-muted);
}

.claims__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.claims__badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: var(--cp-radius-pill);
  font-size: 12px;
  font-weight: 500;
  line-height: 20px;
}

.claims__badge--lost {
  color: var(--cp-error);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  background: color-mix(in srgb, var(--cp-error) 7%, white);
}

.claims__badge--found {
  color: var(--cp-success);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
}

.claims__role {
  font-size: 12px;
  color: var(--cp-info);
}

.claims__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.claims__title {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-ink);
  font-size: 15px;
  font-weight: 600;
}

.claims__meta {
  margin: var(--cp-space-1) 0 0;
  color: var(--cp-muted);
  font-size: 13px;
}

.claims__pagination {
  display: flex;
  justify-content: center;
}

.claims__detail-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.claims__detail-title {
  margin: var(--cp-space-2) 0;
  font-size: 18px;
  color: var(--cp-ink);
}

.claims__detail-meta {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
}

.claims__detail-meta div {
  display: flex;
  gap: var(--cp-space-3);
  font-size: 13px;
}

.claims__detail-meta dt {
  width: 72px;
  color: var(--cp-muted);
  flex-shrink: 0;
}

.claims__detail-meta dd {
  margin: 0;
  color: var(--cp-ink);
}

.claims__evidence {
  margin-top: var(--cp-space-3);
}

.claims__section-title {
  margin: 0 0 var(--cp-space-1);
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink);
}

.claims__evidence-text {
  margin: 0;
  color: var(--cp-body);
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}

.claims__notice {
  margin: var(--cp-space-3) 0 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
  color: var(--cp-warning);
  font-size: 13px;
}

.claims__notice--error {
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
}

.claims__notice--success {
  background: color-mix(in srgb, var(--cp-success) 8%, white);
  color: var(--cp-success);
}

.claims__progress {
  display: flex;
  gap: var(--cp-space-3);
  margin-top: var(--cp-space-3);
  font-size: 12px;
}

.claims__done {
  color: var(--cp-success);
  font-weight: 500;
}

.claims__todo {
  color: var(--cp-muted);
}

.claims__error {
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

.claims__actions {
  display: flex;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-4);
  flex-wrap: wrap;
}

.claims__reject {
  margin-top: var(--cp-space-3);
  padding-top: var(--cp-space-3);
  border-top: 1px solid var(--cp-hairline-soft);
}

.claims__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  background: var(--cp-surface-card);
  resize: vertical;
}

.claims__contact {
  margin-bottom: var(--cp-space-3);
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
}

.claims__contact-label {
  margin: 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.claims__contact-value {
  margin: var(--cp-space-1) 0 0;
  font-size: 15px;
  color: var(--cp-ink);
  font-weight: 600;
  word-break: break-all;
}

.claims__muted {
  margin: 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}
</style>
