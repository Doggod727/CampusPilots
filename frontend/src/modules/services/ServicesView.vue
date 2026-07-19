<script setup lang="ts">
import { ref } from 'vue'

import { ApiError, callApi } from '@/api/client'
import {
  getDepartment,
  getServiceGuide,
  getServiceGuideChecklist,
  listDepartments,
  listServiceGuides,
} from '@/api/generated'
import type {
  DepartmentDetail,
  DepartmentSummary,
  MaterialChecklistData,
  ServiceGuideDetail,
  ServiceGuideSummary,
  StudentType,
} from '@/api/generated'
import { STUDENT_TYPE_LABELS, formatTime } from '@/modules/services/services-utils'
import { useCampusOptions } from '@/modules/services/useCampusOptions'
import { useResourceList } from '@/shared/lib/useResourceList'
import {
  EmptyState,
  ErrorState,
  PageHeader,
  UiButton,
  UiCard,
  UiField,
  UiPagination,
  UiSkeleton,
} from '@/shared/ui'

/* ---------- 校区选项字典（后端联系人聚合，不硬编码） ---------- */
const { options: campusOptions } = useCampusOptions()

/* ---------- 部门浏览（listDepartments/getDepartment，只展示后端返回字段） ---------- */
const departments = ref<DepartmentSummary[]>([])
const departmentsLoading = ref(true)
const departmentsFailed = ref(false)
const departmentQuery = ref('')
const departmentCampus = ref('')

async function loadDepartments() {
  departmentsLoading.value = true
  departmentsFailed.value = false
  try {
    const response = await callApi(() =>
      listDepartments({
        query: {
          ...(departmentQuery.value.trim() ? { q: departmentQuery.value.trim() } : {}),
          ...(departmentCampus.value ? { campus_code: departmentCampus.value } : {}),
        },
      }),
    )
    departments.value = response.data.items
  } catch {
    departmentsFailed.value = true
  } finally {
    departmentsLoading.value = false
  }
}

const departmentDialogOpen = ref(false)
const activeDepartment = ref<DepartmentSummary | null>(null)
const departmentDetail = ref<DepartmentDetail | null>(null)
const departmentDetailLoading = ref(false)
const departmentDetailFailed = ref(false)

async function openDepartment(item: DepartmentSummary) {
  activeDepartment.value = item
  departmentDialogOpen.value = true
  departmentDetail.value = null
  departmentDetailLoading.value = true
  departmentDetailFailed.value = false
  try {
    const response = await callApi(() => getDepartment({ path: { department_id: item.id } }))
    departmentDetail.value = response.data
  } catch {
    departmentDetailFailed.value = true
  } finally {
    departmentDetailLoading.value = false
  }
}

async function retryDepartment() {
  const item = activeDepartment.value
  if (item) {
    await openDepartment(item)
  }
}

/* ---------- 办事指南搜索/筛选（listServiceGuides 分页） ---------- */
const guideQuery = ref('')
const guideCampus = ref('')
const guideStudentType = ref('')
const guideDepartmentId = ref('')

const {
  items: guides,
  total: guidesTotal,
  page: guidesPage,
  pageSize: guidesPageSize,
  loading: guidesLoading,
  failed: guidesFailed,
  load: loadGuides,
  changePage: changeGuidesPage,
} = useResourceList<ServiceGuideSummary>(async (page, pageSize) => {
  const response = await callApi(() =>
    listServiceGuides({
      query: {
        page,
        page_size: pageSize,
        ...(guideQuery.value.trim() ? { q: guideQuery.value.trim() } : {}),
        ...(guideCampus.value ? { campus_code: guideCampus.value } : {}),
        ...(guideStudentType.value ? { student_type: guideStudentType.value as StudentType } : {}),
        ...(guideDepartmentId.value ? { department_id: guideDepartmentId.value } : {}),
      },
    }),
  )
  return { items: response.data.items, total: response.data.pagination.total }
}, 10)

async function applyGuideFilters() {
  guidesPage.value = 1
  await loadGuides()
}

/* ---------- 指南详情 + 按校区/学生类型的材料清单 ---------- */
const guideDialogOpen = ref(false)
const activeGuide = ref<ServiceGuideSummary | null>(null)
const detailCampus = ref('')
const detailStudentType = ref('')
const guideDetail = ref<ServiceGuideDetail | null>(null)
const checklist = ref<MaterialChecklistData | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

async function openGuide(guide: ServiceGuideSummary) {
  activeGuide.value = guide
  detailCampus.value = guideCampus.value
  detailStudentType.value = guideStudentType.value
  guideDetail.value = null
  checklist.value = null
  detailError.value = ''
  guideDialogOpen.value = true
  await loadGuideDetail()
}

async function loadGuideDetail() {
  const guide = activeGuide.value
  if (!guide || !detailCampus.value || !detailStudentType.value) {
    guideDetail.value = null
    checklist.value = null
    return
  }
  detailLoading.value = true
  detailError.value = ''
  try {
    const query = {
      campus_code: detailCampus.value,
      student_type: detailStudentType.value as StudentType,
    }
    const [detailResponse, checklistResponse] = await Promise.all([
      callApi(() => getServiceGuide({ path: { guide_id: guide.id }, query })),
      callApi(() => getServiceGuideChecklist({ path: { guide_id: guide.id }, query })),
    ])
    guideDetail.value = detailResponse.data
    checklist.value = checklistResponse.data
  } catch (error) {
    guideDetail.value = null
    checklist.value = null
    detailError.value =
      error instanceof ApiError && error.status === 404
        ? '指南不存在或已下线'
        : '加载失败，请稍后重试'
  } finally {
    detailLoading.value = false
  }
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString('zh-CN')
}
</script>

<template>
  <div class="services">
    <PageHeader title="校园服务" subtitle="办事指南、材料清单与部门联系方式（内容均来自后端）" />

    <section class="services__section" aria-label="办事指南">
      <h2 class="services__heading">办事指南</h2>
      <div class="services__filters">
        <input
          v-model="guideQuery"
          class="services__input services__input--search"
          type="search"
          placeholder="搜索指南标题或摘要"
          maxlength="100"
          aria-label="搜索指南"
          @keyup.enter="applyGuideFilters"
        />
        <select v-model="guideCampus" class="services__input" aria-label="校区筛选" @change="applyGuideFilters">
          <option value="">全部校区</option>
          <option v-for="code in campusOptions" :key="code" :value="code">{{ code }}</option>
        </select>
        <select
          v-model="guideStudentType"
          class="services__input"
          aria-label="学生类型筛选"
          @change="applyGuideFilters"
        >
          <option value="">全部学生类型</option>
          <option value="undergraduate">本科生</option>
          <option value="postgraduate">研究生</option>
          <option value="international">留学生</option>
          <option value="all">全部学生</option>
        </select>
        <select
          v-model="guideDepartmentId"
          class="services__input"
          aria-label="部门筛选"
          @change="applyGuideFilters"
        >
          <option value="">全部部门</option>
          <option v-for="dept in departments" :key="dept.id" :value="dept.id">{{ dept.name }}</option>
        </select>
        <UiButton @click="applyGuideFilters">搜索</UiButton>
      </div>

      <UiSkeleton v-if="guidesLoading" :lines="4" />
      <ErrorState v-else-if="guidesFailed" title="指南列表加载失败" @retry="loadGuides" />
      <EmptyState
        v-else-if="guides.length === 0"
        title="没有找到符合条件的办事指南"
        description="换个关键词或筛选条件试试"
      />
      <template v-else>
        <div class="services__list">
          <UiCard
            v-for="guide in guides"
            :key="guide.id"
            class="services__item"
            padding="md"
            @click="openGuide(guide)"
          >
            <div class="services__item-head">
              <strong class="services__item-title">{{ guide.title }}</strong>
              <span class="services__badge">{{ guide.category.name }}</span>
              <time class="services__time">更新于 {{ formatTime(guide.updated_at) }}</time>
            </div>
            <p class="services__summary">{{ guide.summary }}</p>
            <p class="services__meta">
              <span>{{ guide.department.name }}</span>
              <span v-if="guide.location">地点：{{ guide.location }}</span>
              <span v-if="guide.service_hours">时间：{{ guide.service_hours }}</span>
            </p>
          </UiCard>
        </div>
        <div class="services__pagination">
          <UiPagination :page="guidesPage" :total="guidesTotal" :page-size="guidesPageSize" @change="changeGuidesPage" />
        </div>
      </template>
    </section>

    <section class="services__section" aria-label="服务部门">
      <h2 class="services__heading">服务部门</h2>
      <div class="services__filters">
        <input
          v-model="departmentQuery"
          class="services__input services__input--search"
          type="search"
          placeholder="搜索部门名称"
          maxlength="100"
          aria-label="搜索部门"
          @keyup.enter="loadDepartments"
        />
        <select
          v-model="departmentCampus"
          class="services__input"
          aria-label="按校区筛选部门"
          @change="loadDepartments"
        >
          <option value="">全部校区</option>
          <option v-for="code in campusOptions" :key="code" :value="code">{{ code }}</option>
        </select>
        <UiButton @click="loadDepartments">搜索</UiButton>
      </div>

      <UiSkeleton v-if="departmentsLoading" :lines="3" />
      <ErrorState v-else-if="departmentsFailed" title="部门列表加载失败" @retry="loadDepartments" />
      <EmptyState v-else-if="departments.length === 0" title="暂无部门信息" description="当前筛选条件下没有部门" />
      <div v-else class="services__list">
        <UiCard
          v-for="dept in departments"
          :key="dept.id"
          class="services__item"
          padding="md"
          @click="openDepartment(dept)"
        >
          <div class="services__item-head">
            <strong class="services__item-title">{{ dept.name }}</strong>
            <code class="services__code">{{ dept.code }}</code>
          </div>
          <p v-if="dept.description" class="services__summary">{{ dept.description }}</p>
        </UiCard>
      </div>
    </section>

    <el-dialog v-model="guideDialogOpen" :title="activeGuide?.title ?? '指南详情'" width="720px">
      <div class="guide">
        <div class="guide__selectors">
          <UiField label="校区" input-id="guide-campus" required>
            <select id="guide-campus" v-model="detailCampus" class="services__input" @change="loadGuideDetail">
              <option value="" disabled>请选择校区</option>
              <option v-for="code in campusOptions" :key="code" :value="code">{{ code }}</option>
            </select>
          </UiField>
          <UiField label="学生类型" input-id="guide-student-type" required>
            <select
              id="guide-student-type"
              v-model="detailStudentType"
              class="services__input"
              @change="loadGuideDetail"
            >
              <option value="" disabled>请选择学生类型</option>
              <option value="undergraduate">本科生</option>
              <option value="postgraduate">研究生</option>
              <option value="international">留学生</option>
              <option value="all">全部学生</option>
            </select>
          </UiField>
        </div>

        <EmptyState
          v-if="!detailCampus || !detailStudentType"
          title="请选择校区和学生类型"
          description="材料清单与适用性按校区和学生类型生成"
        />
        <UiSkeleton v-else-if="detailLoading" :lines="5" />
        <ErrorState v-else-if="detailError" title="指南详情加载失败" :message="detailError" @retry="loadGuideDetail" />
        <template v-else-if="guideDetail">
          <p class="guide__summary">{{ guideDetail.summary }}</p>
          <dl class="guide__facts">
            <div class="guide__fact">
              <dt>分类</dt>
              <dd>{{ guideDetail.category.name }}</dd>
            </div>
            <div class="guide__fact">
              <dt>负责部门</dt>
              <dd>{{ guideDetail.department.name }}</dd>
            </div>
            <div v-if="guideDetail.location" class="guide__fact">
              <dt>办理地点</dt>
              <dd>{{ guideDetail.location }}</dd>
            </div>
            <div v-if="guideDetail.service_hours" class="guide__fact">
              <dt>服务时间</dt>
              <dd>{{ guideDetail.service_hours }}</dd>
            </div>
            <div v-if="guideDetail.valid_until" class="guide__fact">
              <dt>有效期至</dt>
              <dd>{{ formatDate(guideDetail.valid_until) }}</dd>
            </div>
            <div v-if="guideDetail.source_url" class="guide__fact">
              <dt>来源</dt>
              <dd>
                <a :href="guideDetail.source_url" target="_blank" rel="noopener noreferrer">官方页面</a>
              </dd>
            </div>
          </dl>

          <p
            class="guide__applicability"
            :class="{ 'guide__applicability--off': !guideDetail.applicability.applicable }"
            role="status"
          >
            {{ guideDetail.applicability.applicable ? '适用于所选校区与学生类型' : '不适用于所选校区与学生类型' }}
            <span v-if="guideDetail.applicability.notes">（{{ guideDetail.applicability.notes }}）</span>
          </p>

          <section v-if="checklist" class="guide__block" aria-label="材料清单">
            <h3 class="guide__block-title">
              材料清单（{{ checklist.campus_code }} · {{ STUDENT_TYPE_LABELS[checklist.student_type] }}）
            </h3>
            <EmptyState
              v-if="!checklist.applicable"
              title="该指南不适用于所选条件"
              :description="checklist.applicability_reason ?? '当前校区/学生类型无需办理该事项'"
            />
            <EmptyState v-else-if="checklist.materials.length === 0" title="无需准备材料" />
            <ul v-else class="guide__materials">
              <li
                v-for="material in checklist.materials"
                :key="material.id"
                :class="{ 'guide__material--excluded': !material.included }"
              >
                <div class="guide__material-head">
                  <strong>{{ material.name }}</strong>
                  <span v-if="material.required" class="guide__badge guide__badge--required">必需</span>
                  <span v-else class="guide__badge">可选</span>
                  <span v-if="!material.included" class="guide__badge">无需准备</span>
                  <span class="guide__copies">× {{ material.copies }} 份</span>
                </div>
                <p v-if="material.description" class="guide__material-desc">{{ material.description }}</p>
                <p class="guide__material-reason">{{ material.inclusion_reason }}</p>
              </li>
            </ul>
          </section>

          <section v-if="guideDetail.steps.length > 0" class="guide__block" aria-label="办理步骤">
            <h3 class="guide__block-title">办理步骤</h3>
            <ol class="guide__steps">
              <li v-for="step in guideDetail.steps" :key="step.step_no">
                <strong>{{ step.step_no }}. {{ step.title }}</strong>
                <p>{{ step.description }}</p>
                <p v-if="step.location || step.estimated_minutes" class="guide__step-meta">
                  <span v-if="step.location">{{ step.location }}</span>
                  <span v-if="step.estimated_minutes">约 {{ step.estimated_minutes }} 分钟</span>
                </p>
              </li>
            </ol>
          </section>

          <section v-if="guideDetail.contacts.length > 0" class="guide__block" aria-label="联系方式">
            <h3 class="guide__block-title">联系方式</h3>
            <ul class="services__contacts">
              <li v-for="contact in guideDetail.contacts" :key="contact.id">
                <strong>{{ contact.office_name }}</strong>
                <span v-if="contact.contact_name">{{ contact.contact_name }}</span>
                <span v-if="contact.phone">电话：{{ contact.phone }}</span>
                <span v-if="contact.email">邮箱：{{ contact.email }}</span>
                <span>地点：{{ contact.location }}</span>
                <span v-if="contact.office_hours">办公时间：{{ contact.office_hours }}</span>
                <span class="services__campus">{{ contact.campus_code }}</span>
              </li>
            </ul>
          </section>
        </template>
      </div>
    </el-dialog>

    <el-dialog v-model="departmentDialogOpen" :title="activeDepartment?.name ?? '部门详情'" width="640px">
      <UiSkeleton v-if="departmentDetailLoading" :lines="4" />
      <ErrorState v-else-if="departmentDetailFailed" title="部门详情加载失败" @retry="retryDepartment" />
      <template v-else-if="departmentDetail">
        <p v-if="departmentDetail.description" class="guide__summary">{{ departmentDetail.description }}</p>
        <EmptyState
          v-if="departmentDetail.contacts.length === 0"
          title="暂无有效联系方式"
          description="该部门当前没有公布联系方式"
        />
        <ul v-else class="services__contacts">
          <li v-for="contact in departmentDetail.contacts" :key="contact.id">
            <strong>{{ contact.office_name }}</strong>
            <span v-if="contact.contact_name">{{ contact.contact_name }}</span>
            <span v-if="contact.phone">电话：{{ contact.phone }}</span>
            <span v-if="contact.email">邮箱：{{ contact.email }}</span>
            <span>地点：{{ contact.location }}</span>
            <span v-if="contact.office_hours">办公时间：{{ contact.office_hours }}</span>
            <span class="services__campus">{{ contact.campus_code }}</span>
          </li>
        </ul>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.services {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-6);
}

.services__section {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.services__heading {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--cp-ink);
}

.services__filters {
  display: flex;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
  align-items: center;
}

.services__input {
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

.services__input--search {
  flex: 1;
  min-width: 200px;
}

.services__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.services__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.services__item:hover {
  border-color: var(--cp-muted);
}

.services__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.services__item-title {
  font-size: 15px;
  color: var(--cp-ink);
}

.services__badge {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.services__code {
  font-size: 12px;
  color: var(--cp-muted);
  font-family: var(--cp-font-mono);
}

.services__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.services__summary {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-body);
  font-size: 13px;
}

.services__meta {
  margin: var(--cp-space-2) 0 0;
  display: flex;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--cp-muted);
}

.services__pagination {
  display: flex;
  justify-content: center;
}

.services__contacts {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.services__contacts li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  font-size: 13px;
  color: var(--cp-body);
}

.services__contacts strong {
  color: var(--cp-ink);
}

.services__campus {
  font-size: 12px;
  color: var(--cp-muted-soft);
  font-family: var(--cp-font-mono);
}

.guide {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.guide__selectors {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--cp-space-3);
}

.guide__summary {
  margin: 0;
  color: var(--cp-body);
  font-size: 14px;
}

.guide__facts {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--cp-space-2) var(--cp-space-4);
}

.guide__fact {
  display: flex;
  gap: var(--cp-space-2);
  font-size: 13px;
}

.guide__fact dt {
  color: var(--cp-muted);
  white-space: nowrap;
}

.guide__fact dd {
  margin: 0;
  color: var(--cp-ink);
}

.guide__applicability {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-success) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
  color: var(--cp-success);
  font-size: 13px;
}

.guide__applicability--off {
  border-color: color-mix(in srgb, var(--cp-warning) 35%, transparent);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
  color: var(--cp-warning);
}

.guide__block {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.guide__block-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--cp-ink);
}

.guide__materials {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.guide__materials li {
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
}

.guide__material--excluded {
  opacity: 0.6;
}

.guide__material-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--cp-ink);
}

.guide__badge {
  font-size: 12px;
  color: var(--cp-muted);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  padding: 1px 8px;
}

.guide__badge--required {
  color: var(--cp-error);
  border-color: color-mix(in srgb, var(--cp-error) 35%, transparent);
}

.guide__copies {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted);
}

.guide__material-desc {
  margin: var(--cp-space-1) 0 0;
  font-size: 13px;
  color: var(--cp-body);
}

.guide__material-reason {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.guide__steps {
  margin: 0;
  padding-left: var(--cp-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
}

.guide__steps strong {
  color: var(--cp-ink);
}

.guide__steps p {
  margin: 2px 0 0;
}

.guide__step-meta {
  display: flex;
  gap: var(--cp-space-3);
  font-size: 12px;
  color: var(--cp-muted);
}
</style>
