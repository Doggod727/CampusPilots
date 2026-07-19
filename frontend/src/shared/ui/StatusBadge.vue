<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    status: string
    label?: string
  }>(),
  { label: '' },
)

const STATUS_META: Record<
  string,
  { text: string; tone: 'success' | 'error' | 'warning' | 'info' | 'muted' }
> = {
  succeeded: { text: '成功', tone: 'success' },
  completed: { text: '已完成', tone: 'success' },
  published: { text: '已发布', tone: 'success' },
  active: { text: '启用', tone: 'success' },
  valid: { text: '校验通过', tone: 'success' },
  failed: { text: '失败', tone: 'error' },
  rejected: { text: '已拒绝', tone: 'error' },
  invalid: { text: '校验失败', tone: 'error' },
  expired: { text: '已过期', tone: 'error' },
  running: { text: '运行中', tone: 'info' },
  training: { text: '训练中', tone: 'info' },
  evaluating: { text: '评估中', tone: 'info' },
  preparing: { text: '准备中', tone: 'info' },
  streaming: { text: '生成中', tone: 'info' },
  awaiting_approval: { text: '待审批', tone: 'warning' },
  pending: { text: '待处理', tone: 'warning' },
  queued: { text: '排队中', tone: 'warning' },
  partial: { text: '部分完成', tone: 'warning' },
  cancelled: { text: '已取消', tone: 'muted' },
  deleted: { text: '已删除', tone: 'muted' },
  inactive: { text: '停用', tone: 'muted' },
  fallback: { text: '兜底回答', tone: 'muted' },
  created: { text: '已创建', tone: 'muted' },
  routing: { text: '路由中', tone: 'info' },
}

const meta = computed(
  () => STATUS_META[props.status] ?? { text: props.status, tone: 'muted' as const },
)
</script>

<template>
  <span class="status-badge" :class="`status-badge--${meta.tone}`">
    <span class="status-badge__dot" aria-hidden="true" />
    <span>{{ label || meta.text }}</span>
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: var(--cp-radius-pill);
  border: 1px solid var(--cp-hairline);
  font-size: 12px;
  font-weight: 500;
  line-height: 20px;
  white-space: nowrap;
}

.status-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status-badge--success {
  color: var(--cp-success);
  border-color: color-mix(in srgb, var(--cp-success) 35%, transparent);
  background: color-mix(in srgb, var(--cp-success) 7%, white);
}

.status-badge--error {
  color: var(--cp-error);
  border-color: color-mix(in srgb, var(--cp-error) 35%, transparent);
  background: color-mix(in srgb, var(--cp-error) 7%, white);
}

.status-badge--warning {
  color: var(--cp-warning);
  border-color: color-mix(in srgb, var(--cp-warning) 35%, transparent);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
}

.status-badge--info {
  color: var(--cp-info);
  border-color: color-mix(in srgb, var(--cp-info) 35%, transparent);
  background: color-mix(in srgb, var(--cp-info) 7%, white);
}

.status-badge--muted {
  color: var(--cp-muted);
  background: var(--cp-canvas-soft);
}
</style>
