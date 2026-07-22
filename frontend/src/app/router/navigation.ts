export interface NavItem {
  name: string
  title: string
  group: string
  permissions: readonly string[]
}

/** 声明式导航注册表：菜单项所需权限码与后端种子权限一一对应。 */
export const NAV_ITEMS: readonly NavItem[] = [
  { name: 'dashboard', title: '概览', group: '工作台', permissions: ['dashboard:read'] },
  { name: 'services', title: '办事指南', group: '校园服务', permissions: ['service:read'] },
  {
    name: 'work-orders-mine',
    title: '我的工单',
    group: '校园服务',
    permissions: ['work_order:read'],
  },
  {
    name: 'work-orders-handle',
    title: '工单处理',
    group: '校园服务',
    permissions: ['work_order:transition'],
  },
  {
    name: 'electricity',
    title: '电费查询',
    group: '校园服务',
    permissions: ['electricity:read_own'],
  },
  {
    name: 'knowledge-bases',
    title: '知识库管理',
    group: '知识库',
    permissions: ['knowledge:read'],
  },
  {
    name: 'knowledge-ingestion',
    title: '文档入库',
    group: '知识库',
    permissions: ['knowledge:publish'],
  },
  { name: 'community-topics', title: '社区话题', group: '社区', permissions: ['community:read'] },
  { name: 'community-events', title: '校园活动', group: '社区', permissions: ['community:read'] },
  { name: 'lost-found', title: '失物招领', group: '社区', permissions: ['community:read'] },
  { name: 'moderation-cases', title: '审核案件', group: '社区', permissions: ['moderation:read'] },
  { name: 'agent-runs', title: 'Agent 工作台', group: 'Agent', permissions: ['agent:run', 'model:read'] },
  {
    name: 'agent-catalog',
    title: 'Agent 目录',
    group: 'Agent',
    permissions: ['agent:catalog:read', 'model:read'],
  },
  { name: 'tool-catalog', title: 'Tool 目录', group: 'Agent', permissions: ['tool:catalog:read', 'model:read'] },
  { name: 'datasets', title: '数据集', group: 'ModelOps', permissions: ['dataset:read'] },
  { name: 'training-jobs', title: '训练任务', group: 'ModelOps', permissions: ['training:read'] },
  { name: 'models', title: '模型与评估', group: 'ModelOps', permissions: ['model:read'] },
  { name: 'admin-users', title: '用户管理', group: '管理', permissions: ['user:read'] },
  { name: 'admin-roles', title: '角色权限', group: '管理', permissions: ['role:read'] },
  { name: 'admin-words', title: '敏感词', group: '管理', permissions: ['sensitive_word:read'] },
  { name: 'admin-audit', title: '审计日志', group: '管理', permissions: ['audit:read'] },
  { name: 'admin-config', title: '业务配置', group: '管理', permissions: ['config:read'] },
]

export interface NavGroup {
  title: string
  items: NavItem[]
}

/** 按用户权限过滤：无权限菜单整体隐藏，保持声明顺序与分组结构。 */
export function filteredNav(hasPermission: (code: string) => boolean): NavGroup[] {
  const groups: NavGroup[] = []
  for (const item of NAV_ITEMS) {
    if (!item.permissions.every(hasPermission)) {
      continue
    }
    const group = groups.find((entry) => entry.title === item.group)
    if (group) {
      group.items.push(item)
    } else {
      groups.push({ title: item.group, items: [item] })
    }
  }
  return groups
}
