import { createRouter, createWebHistory } from 'vue-router'

import { bootstrapAuth } from '@/app/bootstrap/authBootstrap'
import AppShell from '@/app/layouts/AppShell.vue'
import AgentCatalogView from '@/modules/agent-workbench/AgentCatalogView.vue'
import RunCreateView from '@/modules/agent-workbench/RunCreateView.vue'
import RunDetailView from '@/modules/agent-workbench/RunDetailView.vue'
import RunListView from '@/modules/agent-workbench/RunListView.vue'
import { useAuthStore } from '@/modules/auth/stores/auth'
import LoginPage from '@/modules/auth/LoginPage.vue'

import ComponentsShowcase from './ComponentsShowcase.vue'
import DashboardView from './DashboardView.vue'
import ForbiddenView from './ForbiddenView.vue'
import NotFoundView from './NotFoundView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginPage, meta: { public: true } },
    {
      path: '/',
      component: AppShell,
      children: [
        { path: '', name: 'dashboard', component: DashboardView, meta: { title: '概览', permissions: [] } },
        { path: '403', name: 'forbidden', component: ForbiddenView, meta: { title: '无权限', permissions: [] } },
        { path: 'dev/components', name: 'dev-components', component: ComponentsShowcase, meta: { title: '基础组件', permissions: [] } },
        // M5 Agent Workbench
        { path: 'agent/catalog', name: 'agent-catalog', component: AgentCatalogView, meta: { title: 'Agent 目录', permissions: ['agent:catalog:read'] } },
        { path: 'agent/runs', name: 'agent-runs', component: RunListView, meta: { title: 'Agent 工作台', permissions: ['agent:run'] } },
        { path: 'agent/runs/new', name: 'agent-run-new', component: RunCreateView, meta: { title: '创建 Agent Run', permissions: ['agent:run'] } },
        { path: 'agent/runs/:runId', name: 'agent-run-detail', component: RunDetailView, meta: { title: 'Run 详情', permissions: ['agent:run'] } },
        // M5 Tool 目录与 ModelOps
        { path: 'agent/tools', name: 'tool-catalog', component: () => import('@/modules/modelops/ToolCatalogView.vue'), meta: { title: 'Tool 目录', permissions: ['tool:catalog:read'] } },
        { path: 'modelops/datasets', name: 'datasets', component: () => import('@/modules/modelops/DatasetsView.vue'), meta: { title: '数据集', permissions: ['dataset:read'] } },
        { path: 'modelops/training', name: 'training-jobs', component: () => import('@/modules/modelops/TrainingJobsView.vue'), meta: { title: '训练任务', permissions: ['training:read'] } },
        { path: 'modelops/models', name: 'models', component: () => import('@/modules/modelops/ModelsView.vue'), meta: { title: '模型与评估', permissions: ['model:read'] } },
        // M2 校园服务中心
        { path: 'services', name: 'services', component: () => import('@/modules/services/ServicesView.vue'), meta: { title: '办事指南', permissions: ['service:read'] } },
        { path: 'services/work-orders', name: 'work-orders-mine', component: () => import('@/modules/services/WorkOrdersView.vue'), props: { mode: 'mine' }, meta: { title: '我的工单', permissions: ['work_order:read'] } },
        { path: 'services/work-orders/handle', name: 'work-orders-handle', component: () => import('@/modules/services/WorkOrdersView.vue'), props: { mode: 'handle' }, meta: { title: '工单处理', permissions: ['work_order:transition'] } },
        { path: 'services/work-orders/:workOrderId', name: 'work-order-detail', component: () => import('@/modules/services/WorkOrderDetailView.vue'), meta: { title: '工单详情', permissions: ['work_order:read'] } },
        { path: 'services/electricity', name: 'electricity', component: () => import('@/modules/services/ElectricityView.vue'), meta: { title: '电费查询', permissions: ['electricity:read_own'] } },
        // M1 知识库与 Chat
        { path: 'chat', name: 'chat', component: () => import('@/modules/chat-kb/ChatView.vue'), meta: { title: '知识问答', permissions: ['chat:use'] } },
        { path: 'knowledge/bases', name: 'knowledge-bases', component: () => import('@/modules/chat-kb/KnowledgeBasesView.vue'), meta: { title: '知识库管理', permissions: ['knowledge:read'] } },
        { path: 'knowledge/ingestion', name: 'knowledge-ingestion', component: () => import('@/modules/chat-kb/IngestionView.vue'), meta: { title: '文档入库', permissions: ['knowledge:publish'] } },
        // M3 社区
        { path: 'community/topics', name: 'community-topics', component: () => import('@/modules/community/TopicsView.vue'), meta: { title: '社区话题', permissions: ['community:read'] } },
        { path: 'community/posts', name: 'community-posts', component: () => import('@/modules/community/PostsView.vue'), meta: { title: '帖子', permissions: ['community:read'] } },
        { path: 'community/posts/:postId', name: 'community-post-detail', component: () => import('@/modules/community/PostDetailView.vue'), meta: { title: '帖子详情', permissions: ['community:read'] } },
        { path: 'community/anonymous-reveal', name: 'anonymous-reveal', component: () => import('@/modules/community/AnonymousRevealView.vue'), meta: { title: '匿名身份反查', permissions: ['community:anonymous_identity:read'] } },
        { path: 'community/events', name: 'community-events', component: () => import('@/modules/community/EventsView.vue'), meta: { title: '校园活动', permissions: ['community:read'] } },
        { path: 'community/lost-found', name: 'lost-found', component: () => import('@/modules/community/LostFoundView.vue'), meta: { title: '失物招领', permissions: ['community:read'] } },
        { path: 'community/lost-found/claims', name: 'lost-found-claims', component: () => import('@/modules/community/ClaimsView.vue'), meta: { title: '我的认领', permissions: ['community:read'] } },
        // M4 管理后台
        { path: 'admin/users', name: 'admin-users', component: () => import('@/modules/admin/UsersView.vue'), meta: { title: '用户管理', permissions: ['user:read'] } },
        { path: 'admin/roles', name: 'admin-roles', component: () => import('@/modules/admin/RolesView.vue'), meta: { title: '角色权限', permissions: ['role:read'] } },
        { path: 'admin/words', name: 'admin-words', component: () => import('@/modules/admin/WordsView.vue'), meta: { title: '敏感词', permissions: ['sensitive_word:read'] } },
        { path: 'admin/moderation', name: 'moderation-cases', component: () => import('@/modules/admin/ModerationCasesView.vue'), meta: { title: '审核案件', permissions: ['moderation:read'] } },
        { path: 'admin/audit', name: 'admin-audit', component: () => import('@/modules/admin/AuditView.vue'), meta: { title: '审计日志', permissions: ['audit:read'] } },
        { path: 'admin/config', name: 'admin-config', component: () => import('@/modules/admin/ConfigView.vue'), meta: { title: '业务配置', permissions: ['config:read'] } },
        { path: ':pathMatch(.*)*', name: 'not-found', component: NotFoundView, meta: { title: '页面不存在', permissions: [] } },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  await bootstrapAuth()
  const auth = useAuthStore()
  if (to.meta.public) {
    if (to.name === 'login' && auth.status === 'authenticated') {
      return { name: 'dashboard' }
    }
    return true
  }
  if (auth.status !== 'authenticated') {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  const required = (to.meta.permissions as string[] | undefined) ?? []
  if (required.length > 0 && !required.every((code) => auth.hasPermission(code))) {
    return { name: 'forbidden' }
  }
  return true
})
