<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { callApi } from '@/api/client'
import { createWorkOrder, listWorkOrders } from '@/api/generated'
import type { FaultCategory, WorkOrder, WorkOrderStatus } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import {
  FAULT_CATEGORY_LABELS,
  WORK_ORDER_STATUS_LABELS,
  describeCreateError,
  formatTime,
} from '@/modules/services/services-utils'
import { useCampusOptions } from '@/modules/services/useCampusOptions'
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

const props = defineProps<{ mode: 'mine' | 'handle' }>()

const router = useRouter()
const auth = useAuthStore()

const isHandle = computed(() => props.mode === 'handle')
const canCreate = computed(() => auth.hasPermission('work_order:create'))

const STATUS_FILTERS: Array<{ value: WorkOrderStatus | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'submitted', label: '待受理' },
  { value: 'accepted', label: '已受理' },
  { value: 'processing', label: '处理中' },
  { value: 'completed', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
  { value: 'rejected', label: '已驳回' },
]
const statusFilter = ref<WorkOrderStatus | ''>('')
const campusFilter = ref('')
const assignedOnly = ref(false)

const {
  items: orders,
  total,
  page,
  pageSize,
  loading,
  failed,
  load,
  changePage,
} = useResourceList<WorkOrder>(async (currentPage, currentPageSize) => {
  const response = await callApi(() =>
    listWorkOrders({
      query: {
        page: currentPage,
        page_size: currentPageSize,
        ...(statusFilter.value ? { status: statusFilter.value } : {}),
        ...(campusFilter.value ? { campus_code: campusFilter.value } : {}),
        ...(isHandle.value && assignedOnly.value ? { assigned_to_me: true } : {}),
      },
    }),
  )
  return { items: response.data.items, total: response.data.pagination.total }
}, 10)

async function applyFilters() {
  page.value = 1
  await load()
}

async function changeStatus(value: WorkOrderStatus | '') {
  statusFilter.value = value
  await applyFilters()
}

function openDetail(order: WorkOrder) {
  void router.push({ name: 'work-order-detail', params: { workOrderId: order.id } })
}

/* ---------- 新建工单（幂等键在一次对话框会话内固定，重试复用） ---------- */

const FAULT_CATEGORIES = Object.entries(FAULT_CATEGORY_LABELS) as Array<[FaultCategory, string]>

const { options: campusOptions, loaded: campusLoaded, failed: campusFailed, load: loadCampuses } = useCampusOptions()

const createOpen = ref(false)
const createSubmitting = ref(false)
const createFailure = ref('')
const createKey = ref('')
const createForm = reactive({
  campus_code: '',
  dormitory_area: '',
  building: '',
  room: '',
  fault_category: '' as FaultCategory | '',
  description: '',
  preferred_start_at: '',
  preferred_end_at: '',
})

const canSubmitCreate = computed(
  () =>
    createForm.campus_code.length > 0 &&
    createForm.dormitory_area.trim().length > 0 &&
    createForm.building.trim().length > 0 &&
    createForm.room.trim().length > 0 &&
    createForm.fault_category.length > 0 &&
    createForm.description.trim().length >= 10 &&
    createForm.preferred_start_at.length > 0 &&
    createForm.preferred_end_at.length > 0 &&
    !createSubmitting.value,
)

function openCreate() {
  createForm.campus_code = ''
  createForm.dormitory_area = ''
  createForm.building = ''
  createForm.room = ''
  createForm.fault_category = ''
  createForm.description = ''
  createForm.preferred_start_at = ''
  createForm.preferred_end_at = ''
  createFailure.value = ''
  createKey.value = crypto.randomUUID()
  createOpen.value = true
}

async function submitCreate() {
  if (!canSubmitCreate.value) {
    return
  }
  const start = new Date(createForm.preferred_start_at)
  const end = new Date(createForm.preferred_end_at)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) {
    createFailure.value = '期望上门结束时间必须晚于开始时间'
    return
  }
  createSubmitting.value = true
  createFailure.value = ''
  try {
    const response = await callApi(() =>
      createWorkOrder({
        body: {
          campus_code: createForm.campus_code,
          dormitory_area: createForm.dormitory_area.trim(),
          building: createForm.building.trim(),
          room: createForm.room.trim(),
          fault_category: createForm.fault_category as FaultCategory,
          description: createForm.description.trim(),
          preferred_start_at: start.toISOString(),
          preferred_end_at: end.toISOString(),
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createOpen.value = false
    await router.push({ name: 'work-order-detail', params: { workOrderId: response.data.id } })
  } catch (error) {
    createFailure.value = describeCreateError(error)
  } finally {
    createSubmitting.value = false
  }
}
</script>

<template>
  <div class="orders">
    <PageHeader
      :title="isHandle ? '工单处理' : '我的工单'"
      :subtitle="isHandle ? '你权限范围内需要处理的报修工单' : '你提交的宿舍报修工单'"
    >
      <UiButton v-if="canCreate" variant="primary" data-test="open-create" @click="openCreate">新建工单</UiButton>
    </PageHeader>

    <div class="orders__filters">
      <div class="orders__status" role="tablist">
        <button
          v-for="filter in STATUS_FILTERS"
          :key="filter.label"
          type="button"
          class="orders__filter"
          :class="{ 'orders__filter--active': statusFilter === filter.value }"
          @click="changeStatus(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
      <select v-model="campusFilter" class="orders__input" aria-label="校区筛选" @change="applyFilters">
        <option value="">全部校区</option>
        <option v-for="code in campusOptions" :key="code" :value="code">{{ code }}</option>
      </select>
      <label v-if="isHandle" class="orders__check">
        <input v-model="assignedOnly" type="checkbox" @change="applyFilters" />
        只看分配给我的
      </label>
    </div>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="工单列表加载失败" @retry="load" />
    <EmptyState
      v-else-if="orders.length === 0"
      :title="isHandle ? '暂无待处理工单' : '暂无工单'"
      :description="isHandle ? '当前筛选条件下没有需要处理的工单' : '点击右上角「新建工单」提交宿舍报修'"
    />
    <template v-else>
      <div class="orders__list">
        <UiCard
          v-for="order in orders"
          :key="order.id"
          class="orders__item"
          padding="md"
          @click="openDetail(order)"
        >
          <div class="orders__item-head">
            <code class="orders__no">{{ order.order_no }}</code>
            <StatusBadge :status="order.status" :label="WORK_ORDER_STATUS_LABELS[order.status]" />
            <span class="orders__category">{{ FAULT_CATEGORY_LABELS[order.fault_category] }}</span>
            <time class="orders__time">{{ formatTime(order.submitted_at) }}</time>
          </div>
          <p class="orders__summary">{{ order.description }}</p>
          <p class="orders__meta">
            {{ order.campus_code }} · {{ order.dormitory_area }} · {{ order.building }} · {{ order.room }}
          </p>
        </UiCard>
      </div>
      <div class="orders__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <el-dialog v-model="createOpen" title="新建工单" width="640px">
      <ErrorState v-if="campusFailed" title="校区字典加载失败" message="无法获取校区选项" @retry="loadCampuses" />
      <EmptyState
        v-else-if="campusLoaded && campusOptions.length === 0"
        title="暂无可用校区"
        description="后端字典未返回校区选项，请稍后再试"
      />
      <form v-else class="orders__form" data-test="create-form" @submit.prevent="submitCreate">
        <div class="orders__form-grid">
          <UiField label="校区" input-id="wo-campus" required hint="选项来自后端字典">
            <select id="wo-campus" v-model="createForm.campus_code" class="orders__field" :disabled="createSubmitting">
              <option value="" disabled>请选择校区</option>
              <option v-for="code in campusOptions" :key="code" :value="code">{{ code }}</option>
            </select>
          </UiField>
          <UiField label="宿舍区域" input-id="wo-area" required>
            <input
              id="wo-area"
              v-model="createForm.dormitory_area"
              class="orders__field"
              maxlength="100"
              placeholder="例如：东园一舍"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="楼栋" input-id="wo-building" required>
            <input
              id="wo-building"
              v-model="createForm.building"
              class="orders__field"
              maxlength="50"
              placeholder="例如：3 栋"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="房间号" input-id="wo-room" required>
            <input
              id="wo-room"
              v-model="createForm.room"
              class="orders__field"
              maxlength="30"
              placeholder="例如：521"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="故障类别" input-id="wo-category" required>
            <select
              id="wo-category"
              v-model="createForm.fault_category"
              class="orders__field"
              :disabled="createSubmitting"
            >
              <option value="" disabled>请选择故障类别</option>
              <option v-for="[value, label] in FAULT_CATEGORIES" :key="value" :value="value">{{ label }}</option>
            </select>
          </UiField>
        </div>
        <UiField label="问题描述" input-id="wo-description" required hint="10–1000 字">
          <textarea
            id="wo-description"
            v-model="createForm.description"
            class="orders__field"
            rows="4"
            maxlength="1000"
            placeholder="请描述故障现象、位置和影响"
            :disabled="createSubmitting"
          />
        </UiField>
        <div class="orders__form-grid">
          <UiField label="期望上门开始时间" input-id="wo-start" required>
            <input
              id="wo-start"
              v-model="createForm.preferred_start_at"
              class="orders__field"
              type="datetime-local"
              :disabled="createSubmitting"
            />
          </UiField>
          <UiField label="期望上门结束时间" input-id="wo-end" required hint="必须晚于开始时间">
            <input
              id="wo-end"
              v-model="createForm.preferred_end_at"
              class="orders__field"
              type="datetime-local"
              :disabled="createSubmitting"
            />
          </UiField>
        </div>
        <p v-if="createFailure" class="orders__form-error" role="alert">{{ createFailure }}</p>
        <div class="orders__form-actions">
          <UiButton @click="createOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" data-test="wo-submit" :loading="createSubmitting" :disabled="!canSubmitCreate">
            提交工单
          </UiButton>
        </div>
      </form>
    </el-dialog>
  </div>
</template>

<style scoped>
.orders {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.orders__filters {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.orders__status {
  display: flex;
  gap: var(--cp-space-1);
  flex-wrap: wrap;
}

.orders__filter {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.orders__filter--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.orders__input {
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  color: var(--cp-ink);
  background: var(--cp-surface-card);
}

.orders__check {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.orders__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.orders__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.orders__item:hover {
  border-color: var(--cp-muted);
}

.orders__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.orders__no {
  font-size: 12px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.orders__category {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.orders__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.orders__summary {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-ink);
  font-size: 14px;
}

.orders__meta {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.orders__pagination {
  display: flex;
  justify-content: center;
}

.orders__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.orders__form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--cp-space-3);
}

.orders__field {
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

textarea.orders__field {
  resize: vertical;
}

.orders__form-error {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.orders__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
}
</style>
