<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import { getWorkOrder, listWorkOrderEvents, rateWorkOrder, transitionWorkOrder } from '@/api/generated'
import type { WorkOrder, WorkOrderEvent } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import {
  FAULT_CATEGORY_LABELS,
  WORK_ORDER_STATUS_LABELS,
  describeRatingError,
  describeTransitionError,
  formatTime,
  legalTransitions,
  type TransitionAction,
} from '@/modules/services/services-utils'
import {
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  UiButton,
  UiCard,
  UiField,
  UiSkeleton,
} from '@/shared/ui'

const route = useRoute()
const auth = useAuthStore()
const workOrderId = computed(() => route.params.workOrderId as string)

const order = ref<WorkOrder | null>(null)
const events = ref<WorkOrderEvent[]>([])
const loading = ref(true)
const notFound = ref(false)
const failed = ref(false)

async function load() {
  if (!order.value) {
    loading.value = true
  }
  notFound.value = false
  failed.value = false
  try {
    const [orderResponse, eventsResponse] = await Promise.all([
      callApi(() => getWorkOrder({ path: { work_order_id: workOrderId.value } })),
      callApi(() => listWorkOrderEvents({ path: { work_order_id: workOrderId.value } })),
    ])
    order.value = orderResponse.data
    events.value = eventsResponse.data.items
    if (!orderResponse.data.rating) {
      // 评价表单会话固定幂等键：重试复用，成功或重新装载后更新。
      ratingKey.value = crypto.randomUUID()
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound.value = true
      order.value = null
      events.value = []
    } else {
      failed.value = true
    }
  } finally {
    loading.value = false
  }
}

const isOwner = computed(() => order.value !== null && auth.user?.id === order.value.created_by)
const canTransition = computed(() => auth.hasPermission('work_order:transition'))

/** 合法流转 = 后端状态机矩阵 ∩ 当前账号授权（创建者仅可取消 submitted；其余需 work_order:transition）。 */
const actions = computed<TransitionAction[]>(() => {
  const current = order.value
  if (!current) {
    return []
  }
  return legalTransitions(current.status).filter((action) =>
    action.target === 'cancelled' ? isOwner.value || canTransition.value : canTransition.value,
  )
})

/* ---------- 状态流转（version 乐观锁 + 会话固定幂等键） ---------- */

const transitionOpen = ref(false)
const activeAction = ref<TransitionAction | null>(null)
const transitionSubmitting = ref(false)
const transitionFailure = ref('')
const transitionKey = ref('')
const transitionForm = reactive({ reason: '', completion_note: '' })

const canSubmitTransition = computed(() => {
  if (!activeAction.value || transitionSubmitting.value) {
    return false
  }
  if (transitionForm.reason.trim().length < 2) {
    return false
  }
  return !(
    activeAction.value.requiresCompletionNote && transitionForm.completion_note.trim().length === 0
  )
})

function openTransition(action: TransitionAction) {
  activeAction.value = action
  transitionForm.reason = ''
  transitionForm.completion_note = ''
  transitionFailure.value = ''
  transitionKey.value = crypto.randomUUID()
  transitionOpen.value = true
}

async function submitTransition() {
  const current = order.value
  const action = activeAction.value
  if (!current || !action || !canSubmitTransition.value) {
    return
  }
  transitionSubmitting.value = true
  transitionFailure.value = ''
  try {
    await callApi(() =>
      transitionWorkOrder({
        path: { work_order_id: current.id },
        body: {
          target_status: action.target,
          reason: transitionForm.reason.trim(),
          ...(action.requiresCompletionNote
            ? { completion_note: transitionForm.completion_note.trim() }
            : {}),
          version: current.version,
        },
        headers: { 'Idempotency-Key': transitionKey.value },
      }),
    )
    transitionOpen.value = false
    await load()
  } catch (error) {
    transitionFailure.value = describeTransitionError(error)
  } finally {
    transitionSubmitting.value = false
  }
}

/* ---------- 评价（仅创建者、完成后、一次） ---------- */

const canRate = computed(
  () => order.value !== null && order.value.status === 'completed' && !order.value.rating && isOwner.value,
)
const ratingSubmitting = ref(false)
const ratingFailure = ref('')
const ratingKey = ref('')
const ratingForm = reactive({ score: 5, comment: '' })

async function submitRating() {
  const current = order.value
  if (!current || !canRate.value || ratingSubmitting.value) {
    return
  }
  ratingSubmitting.value = true
  ratingFailure.value = ''
  try {
    await callApi(() =>
      rateWorkOrder({
        path: { work_order_id: current.id },
        body: {
          score: ratingForm.score,
          ...(ratingForm.comment.trim() ? { comment: ratingForm.comment.trim() } : {}),
        },
        headers: { 'Idempotency-Key': ratingKey.value || crypto.randomUUID() },
      }),
    )
    ratingForm.comment = ''
    await load()
  } catch (error) {
    ratingFailure.value = describeRatingError(error)
    if (error instanceof ApiError && error.status === 409 && error.code === 'WORK_ORDER_ALREADY_RATED') {
      await load()
    }
  } finally {
    ratingSubmitting.value = false
  }
}

function actorRoleLabel(role: string): string {
  return role === 'student' ? '学生' : role
}

onMounted(load)
</script>

<template>
  <div class="detail">
    <PageHeader title="工单详情" :subtitle="order ? `工单号 ${order.order_no}` : '报修工单状态与处理记录'" />

    <UiSkeleton v-if="loading" :lines="6" />
    <EmptyState
      v-else-if="notFound"
      title="工单不存在或不可见"
      description="该工单可能已被删除，或不在你的可见范围"
    />
    <ErrorState v-else-if="failed" title="工单详情加载失败" @retry="load" />
    <template v-else-if="order">
      <UiCard padding="lg" class="detail__card">
        <div class="detail__head">
          <code class="detail__no">{{ order.order_no }}</code>
          <StatusBadge :status="order.status" :label="WORK_ORDER_STATUS_LABELS[order.status]" />
          <span class="detail__category">{{ FAULT_CATEGORY_LABELS[order.fault_category] }}</span>
        </div>
        <p class="detail__description">{{ order.description }}</p>
        <dl class="detail__facts">
          <div class="detail__fact">
            <dt>报修位置</dt>
            <dd>{{ order.campus_code }} · {{ order.dormitory_area }} · {{ order.building }} · {{ order.room }}</dd>
          </div>
          <div class="detail__fact">
            <dt>期望上门</dt>
            <dd>{{ formatTime(order.preferred_start_at) }} 至 {{ formatTime(order.preferred_end_at) }}</dd>
          </div>
          <div class="detail__fact">
            <dt>提交时间</dt>
            <dd>{{ formatTime(order.submitted_at) }}</dd>
          </div>
          <div v-if="order.accepted_at" class="detail__fact">
            <dt>受理时间</dt>
            <dd>{{ formatTime(order.accepted_at) }}</dd>
          </div>
          <div v-if="order.processing_at" class="detail__fact">
            <dt>开始处理</dt>
            <dd>{{ formatTime(order.processing_at) }}</dd>
          </div>
          <div v-if="order.completed_at" class="detail__fact">
            <dt>完成时间</dt>
            <dd>{{ formatTime(order.completed_at) }}</dd>
          </div>
          <div v-if="order.cancelled_at" class="detail__fact">
            <dt>取消时间</dt>
            <dd>{{ formatTime(order.cancelled_at) }}</dd>
          </div>
          <div v-if="order.rejected_at" class="detail__fact">
            <dt>驳回时间</dt>
            <dd>{{ formatTime(order.rejected_at) }}</dd>
          </div>
        </dl>
        <p v-if="order.rejection_reason" class="detail__note detail__note--error" role="status">
          驳回原因：{{ order.rejection_reason }}
        </p>
        <p v-if="order.completion_note" class="detail__note" role="status">处理说明：{{ order.completion_note }}</p>

        <div v-if="actions.length > 0" class="detail__actions">
          <UiButton
            v-for="action in actions"
            :key="action.target"
            :variant="action.danger ? 'danger' : 'primary'"
            @click="openTransition(action)"
          >
            {{ action.label }}
          </UiButton>
        </div>
      </UiCard>

      <UiCard v-if="order.rating" padding="md" class="detail__rating">
        <h2 class="detail__heading">我的评价</h2>
        <p class="detail__score">{{ '★'.repeat(order.rating.score) }}{{ '☆'.repeat(5 - order.rating.score) }}</p>
        <p v-if="order.rating.comment" class="detail__comment">{{ order.rating.comment }}</p>
        <p class="detail__time">{{ formatTime(order.rating.created_at) }}</p>
      </UiCard>

      <UiCard v-else-if="canRate" padding="md" class="detail__rating">
        <h2 class="detail__heading">评价本次维修</h2>
        <form class="detail__rating-form" @submit.prevent="submitRating">
          <div class="detail__score-picker" role="radiogroup" aria-label="评分">
            <label
              v-for="value in [1, 2, 3, 4, 5]"
              :key="value"
              class="detail__score-option"
              :class="{ 'detail__score-option--active': ratingForm.score === value }"
            >
              <input v-model.number="ratingForm.score" type="radio" name="rating-score" :value="value" class="sr-only" />
              {{ value }} 分
            </label>
          </div>
          <UiField label="评价内容" input-id="rating-comment" hint="选填，最多 500 字">
            <textarea
              id="rating-comment"
              v-model="ratingForm.comment"
              class="detail__input"
              rows="3"
              maxlength="500"
              :disabled="ratingSubmitting"
            />
          </UiField>
          <p v-if="ratingFailure" class="detail__form-error" role="alert">{{ ratingFailure }}</p>
          <div class="detail__form-actions">
            <UiButton variant="primary" type="submit" :loading="ratingSubmitting">提交评价</UiButton>
          </div>
        </form>
      </UiCard>

      <UiCard padding="md" class="detail__timeline-card">
        <h2 class="detail__heading">状态时间线</h2>
        <EmptyState v-if="events.length === 0" title="暂无事件" />
        <ol v-else class="detail__timeline">
          <li v-for="event in events" :key="event.id">
            <div class="detail__event-head">
              <span class="detail__event-flow">
                {{ event.from_status ? WORK_ORDER_STATUS_LABELS[event.from_status] : '创建' }}
                →
                {{ WORK_ORDER_STATUS_LABELS[event.to_status] }}
              </span>
              <StatusBadge :status="event.to_status" :label="WORK_ORDER_STATUS_LABELS[event.to_status]" />
              <span class="detail__actor">{{ actorRoleLabel(event.actor_role) }}</span>
              <time class="detail__time">{{ formatTime(event.created_at) }}</time>
            </div>
            <p v-if="event.reason" class="detail__reason">{{ event.reason }}</p>
          </li>
        </ol>
      </UiCard>
    </template>

    <el-dialog v-model="transitionOpen" :title="activeAction?.label ?? '状态流转'" width="520px">
      <form class="detail__form" @submit.prevent="submitTransition">
        <UiField label="原因说明" input-id="transition-reason" required hint="2–500 字，将记录到时间线">
          <textarea
            id="transition-reason"
            v-model="transitionForm.reason"
            class="detail__input"
            rows="3"
            maxlength="500"
            :disabled="transitionSubmitting"
          />
        </UiField>
        <UiField
          v-if="activeAction?.requiresCompletionNote"
          label="处理说明"
          input-id="transition-note"
          required
          hint="完成工单时必须填写处理结果"
        >
          <textarea
            id="transition-note"
            v-model="transitionForm.completion_note"
            class="detail__input"
            rows="3"
            maxlength="1000"
            :disabled="transitionSubmitting"
          />
        </UiField>
        <p v-if="transitionFailure" class="detail__form-error" role="alert">{{ transitionFailure }}</p>
        <div class="detail__form-actions">
          <UiButton @click="transitionOpen = false">取消</UiButton>
          <UiButton
            :variant="activeAction?.danger ? 'danger' : 'primary'"
            type="submit"
            :loading="transitionSubmitting"
            :disabled="!canSubmitTransition"
          >
            确认{{ activeAction?.label }}
          </UiButton>
        </div>
      </form>
    </el-dialog>
  </div>
</template>

<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
  max-width: 860px;
}

.detail__head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.detail__no {
  font-size: 13px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.detail__category {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.detail__description {
  margin: var(--cp-space-3) 0 0;
  color: var(--cp-ink);
  font-size: 14px;
}

.detail__facts {
  margin: var(--cp-space-4) 0 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
}

.detail__fact {
  display: flex;
  gap: var(--cp-space-2);
  font-size: 13px;
}

.detail__fact dt {
  color: var(--cp-muted);
  white-space: nowrap;
}

.detail__fact dd {
  margin: 0;
  color: var(--cp-ink);
}

.detail__note {
  margin: var(--cp-space-3) 0 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
  color: var(--cp-success);
  font-size: 13px;
}

.detail__note--error {
  border-color: color-mix(in srgb, var(--cp-error) 35%, transparent);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
}

.detail__actions {
  margin-top: var(--cp-space-4);
  display: flex;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.detail__heading {
  margin: 0 0 var(--cp-space-3);
  font-size: 15px;
  font-weight: 600;
  color: var(--cp-ink);
}

.detail__rating-form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.detail__score-picker {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.detail__score-option {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.detail__score-option--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

.detail__score {
  margin: 0;
  font-size: 18px;
  color: var(--cp-warning);
  letter-spacing: 2px;
}

.detail__comment {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-body);
}

.detail__timeline {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.detail__timeline li {
  padding-left: var(--cp-space-3);
  border-left: 2px solid var(--cp-hairline-strong);
}

.detail__event-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.detail__event-flow {
  font-size: 13px;
  color: var(--cp-ink);
}

.detail__actor {
  font-size: 12px;
  color: var(--cp-muted);
}

.detail__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.detail__reason {
  margin: var(--cp-space-1) 0 0;
  font-size: 13px;
  color: var(--cp-body);
}

.detail__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.detail__input {
  width: 100%;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  color: var(--cp-ink);
  background: var(--cp-surface-card);
  box-sizing: border-box;
  resize: vertical;
}

.detail__form-error {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.detail__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
}
</style>
