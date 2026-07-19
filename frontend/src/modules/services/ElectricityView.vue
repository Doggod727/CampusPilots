<script setup lang="ts">
import { computed, ref } from 'vue'

import { ApiError, callApi } from '@/api/client'
import {
  createElectricityTopupRequest,
  getElectricityBalance,
  queryExternalServiceProgress,
} from '@/api/generated'
import type { ElectricityBalance, ElectricityTopup, ServiceProgress } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import {
  describeProgressError,
  describeTopupError,
  formatTime,
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

const auth = useAuthStore()

const UUID_PATTERN = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

function isSimulatedSource(source: string, isSimulated: boolean): boolean {
  return isSimulated || source === 'mock'
}

/* ---------- 余额查询（只允许后端确认绑定的房间；越权/不存在统一安全空态） ---------- */

type BalanceState = 'idle' | 'loading' | 'ready' | 'denied' | 'error'

const roomIdInput = ref('')
const balanceState = ref<BalanceState>('idle')
const balance = ref<ElectricityBalance | null>(null)

const canQueryBalance = computed(() => UUID_PATTERN.test(roomIdInput.value.trim()) && balanceState.value !== 'loading')

async function queryBalance() {
  const roomId = roomIdInput.value.trim()
  if (!UUID_PATTERN.test(roomId)) {
    return
  }
  balanceState.value = 'loading'
  balance.value = null
  try {
    const response = await callApi(() => getElectricityBalance({ path: { room_id: roomId } }))
    balance.value = response.data
    balanceState.value = 'ready'
    // 充值表单会话固定幂等键：重试复用，成功后更新。
    topupKey.value = crypto.randomUUID()
  } catch (error) {
    if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
      balanceState.value = 'denied'
    } else {
      balanceState.value = 'error'
    }
  }
}

function formatCny(value: number): string {
  return `¥${value.toFixed(2)}`
}

/* ---------- 模拟充值（结果带 simulated/mock 标记，显式标注「模拟」） ---------- */

const canTopup = computed(() => auth.hasPermission('electricity:topup_request:create'))
const topupAmount = ref('')
const topupSubmitting = ref(false)
const topupFailure = ref('')
const topupKey = ref('')
const topupResult = ref<ElectricityTopup | null>(null)

const topupAmountValue = computed(() => Number(topupAmount.value))
const canSubmitTopup = computed(
  () =>
    balanceState.value === 'ready' &&
    topupAmount.value.trim().length > 0 &&
    Number.isFinite(topupAmountValue.value) &&
    topupAmountValue.value >= 1 &&
    topupAmountValue.value <= 500 &&
    !topupSubmitting.value,
)

async function submitTopup() {
  const current = balance.value
  if (!current || !canSubmitTopup.value) {
    return
  }
  topupSubmitting.value = true
  topupFailure.value = ''
  topupResult.value = null
  try {
    const response = await callApi(() =>
      createElectricityTopupRequest({
        body: { room_id: current.room_id, amount_cny: topupAmountValue.value },
        headers: { 'Idempotency-Key': topupKey.value || crypto.randomUUID() },
      }),
    )
    topupResult.value = response.data
    topupKey.value = crypto.randomUUID()
    topupAmount.value = ''
    await queryBalance()
  } catch (error) {
    topupFailure.value = describeTopupError(error)
  } finally {
    topupSubmitting.value = false
  }
}

/* ---------- 外部办事进度（超时/无记录/服务关闭状态展示） ---------- */

type ProgressState = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

const SYSTEMS = [
  { value: 'student_affairs', label: '学生事务系统' },
  { value: 'academic_affairs', label: '教务系统' },
] as const

const progressSystem = ref<(typeof SYSTEMS)[number]['value']>('student_affairs')
const businessNo = ref('')
const progressState = ref<ProgressState>('idle')
const progress = ref<ServiceProgress | null>(null)
const progressError = ref('')

const canQueryProgress = computed(
  () => businessNo.value.trim().length >= 6 && businessNo.value.trim().length <= 64 && progressState.value !== 'loading',
)

async function queryProgress() {
  if (!canQueryProgress.value) {
    return
  }
  progressState.value = 'loading'
  progress.value = null
  progressError.value = ''
  try {
    const response = await callApi(() =>
      queryExternalServiceProgress({
        body: { system_code: progressSystem.value, business_no: businessNo.value.trim() },
      }),
    )
    progress.value = response.data
    progressState.value = 'ready'
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      progressState.value = 'empty'
    } else {
      progressError.value = describeProgressError(error)
      progressState.value = 'error'
    }
  }
}
</script>

<template>
  <div class="electricity">
    <PageHeader title="电费与办事进度" subtitle="电费为演示环境数据；外部进度经统一适配器查询" />

    <UiCard padding="lg" class="electricity__card">
      <h2 class="electricity__heading">电费余额</h2>
      <form class="electricity__query" @submit.prevent="queryBalance">
        <UiField label="房间 ID" input-id="room-id" hint="仅可查询后端确认绑定到你账号的房间（UUID）">
          <input
            id="room-id"
            v-model="roomIdInput"
            class="electricity__input"
            placeholder="例如：21000000-0000-4000-8000-000000000001"
            :disabled="balanceState === 'loading'"
          />
        </UiField>
        <UiButton variant="primary" type="submit" :loading="balanceState === 'loading'" :disabled="!canQueryBalance">
          查询余额
        </UiButton>
      </form>

      <UiSkeleton v-if="balanceState === 'loading'" :lines="3" />
      <EmptyState
        v-else-if="balanceState === 'denied'"
        title="无法查询该房间"
        description="该房间未绑定到你的账号，或不存在"
      />
      <ErrorState v-else-if="balanceState === 'error'" title="余额查询失败" @retry="queryBalance" />
      <div v-else-if="balanceState === 'ready' && balance" class="electricity__balance">
        <div class="electricity__balance-head">
          <strong class="electricity__room">{{ balance.room_name }}</strong>
          <span v-if="isSimulatedSource(balance.source, balance.is_simulated)" class="electricity__sim">模拟数据</span>
        </div>
        <p class="electricity__amount">{{ formatCny(balance.balance_cny) }}</p>
        <p class="electricity__asof">数据时间：{{ formatTime(balance.as_of) }}</p>
      </div>

      <div v-if="balanceState === 'ready' && balance && canTopup" class="electricity__topup">
        <h3 class="electricity__subheading">模拟充值</h3>
        <p class="electricity__hint">演示申请，不产生真实扣款或到账；金额 1.00–500.00 元。</p>
        <form class="electricity__topup-form" @submit.prevent="submitTopup">
          <UiField label="充值金额（元）" input-id="topup-amount" required>
            <input
              id="topup-amount"
              v-model="topupAmount"
              class="electricity__input"
              type="number"
              min="1"
              max="500"
              step="0.01"
              placeholder="例如：50.00"
              :disabled="topupSubmitting"
            />
          </UiField>
          <UiButton variant="primary" type="submit" :loading="topupSubmitting" :disabled="!canSubmitTopup">
            提交模拟充值
          </UiButton>
        </form>
        <p v-if="topupFailure" class="electricity__form-error" role="alert">{{ topupFailure }}</p>
        <div v-if="topupResult" class="electricity__topup-result" role="status">
          <div class="electricity__balance-head">
            <strong>充值申请已创建（模拟）</strong>
            <span v-if="isSimulatedSource(topupResult.source, topupResult.is_simulated)" class="electricity__sim">
              模拟
            </span>
          </div>
          <p>金额：{{ formatCny(topupResult.amount_cny) }}</p>
          <p>状态：{{ topupResult.status }}</p>
          <p>申请号：<code>{{ topupResult.request_id }}</code></p>
          <p>{{ topupResult.notice }}</p>
        </div>
      </div>
    </UiCard>

    <UiCard padding="lg" class="electricity__card">
      <h2 class="electricity__heading">外部办事进度</h2>
      <form class="electricity__progress-form" @submit.prevent="queryProgress">
        <UiField label="校园系统" input-id="progress-system">
          <select id="progress-system" v-model="progressSystem" class="electricity__input" :disabled="progressState === 'loading'">
            <option v-for="system in SYSTEMS" :key="system.value" :value="system.value">{{ system.label }}</option>
          </select>
        </UiField>
        <UiField label="业务号" input-id="progress-no" hint="6–64 位，后端只保存脱敏摘要">
          <input
            id="progress-no"
            v-model="businessNo"
            class="electricity__input"
            maxlength="64"
            placeholder="输入外部系统的业务号"
            :disabled="progressState === 'loading'"
          />
        </UiField>
        <UiButton variant="primary" type="submit" :loading="progressState === 'loading'" :disabled="!canQueryProgress">
          查询进度
        </UiButton>
      </form>

      <UiSkeleton v-if="progressState === 'loading'" :lines="3" />
      <EmptyState
        v-else-if="progressState === 'empty'"
        title="未查询到办理记录"
        description="请核对业务号是否输入正确"
      />
      <ErrorState v-else-if="progressState === 'error'" title="进度查询失败" :message="progressError" @retry="queryProgress" />
      <div v-else-if="progressState === 'ready' && progress" class="electricity__progress" role="status">
        <div class="electricity__balance-head">
          <StatusBadge :status="progress.status" :label="progress.status_text" />
          <span v-if="progress.source === 'mock'" class="electricity__sim">模拟数据</span>
        </div>
        <dl class="electricity__facts">
          <div class="electricity__fact">
            <dt>业务号</dt>
            <dd>{{ progress.business_no_masked }}</dd>
          </div>
          <div class="electricity__fact">
            <dt>更新时间</dt>
            <dd>{{ formatTime(progress.updated_at) }}</dd>
          </div>
          <div v-if="progress.next_action" class="electricity__fact">
            <dt>下一步</dt>
            <dd>{{ progress.next_action }}</dd>
          </div>
        </dl>
      </div>
    </UiCard>
  </div>
</template>

<style scoped>
.electricity {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
  max-width: 720px;
}

.electricity__card {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.electricity__heading {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--cp-ink);
}

.electricity__subheading {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--cp-ink);
}

.electricity__hint {
  margin: 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.electricity__query,
.electricity__topup-form,
.electricity__progress-form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
  max-width: 420px;
}

.electricity__input {
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

.electricity__balance {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
  padding: var(--cp-space-4);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  background: var(--cp-canvas-soft);
}

.electricity__balance-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.electricity__room {
  font-size: 14px;
  color: var(--cp-ink);
}

.electricity__sim {
  font-size: 12px;
  color: var(--cp-warning);
  border: 1px solid color-mix(in srgb, var(--cp-warning) 35%, transparent);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.electricity__amount {
  margin: 0;
  font-size: 28px;
  font-weight: 600;
  color: var(--cp-ink);
}

.electricity__asof {
  margin: 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.electricity__topup {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
  padding-top: var(--cp-space-4);
  border-top: 1px solid var(--cp-hairline);
}

.electricity__form-error {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.electricity__topup-result {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-warning) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
  font-size: 13px;
  color: var(--cp-body);
}

.electricity__topup-result p {
  margin: 0;
}

.electricity__topup-result code {
  font-family: var(--cp-font-mono);
  font-size: 12px;
}

.electricity__progress {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
  padding: var(--cp-space-4);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  background: var(--cp-canvas-soft);
}

.electricity__facts {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.electricity__fact {
  display: flex;
  gap: var(--cp-space-2);
  font-size: 13px;
}

.electricity__fact dt {
  color: var(--cp-muted);
  white-space: nowrap;
}

.electricity__fact dd {
  margin: 0;
  color: var(--cp-ink);
}
</style>
