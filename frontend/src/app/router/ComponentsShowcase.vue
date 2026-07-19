<script setup lang="ts">
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
import { ref } from 'vue'

const page = ref(3)
const statuses = [
  'succeeded',
  'running',
  'awaiting_approval',
  'queued',
  'failed',
  'cancelled',
  'partial',
  'fallback',
]
</script>

<template>
  <div class="showcase">
    <PageHeader
      eyebrow="Design System"
      title="基础组件"
      subtitle="DESIGN 令牌驱动的共享组件（开发工具页）"
    />

    <UiCard class="section">
      <h2>按钮</h2>
      <div class="row">
        <UiButton variant="primary"> 主要操作 </UiButton>
        <UiButton>次要操作</UiButton>
        <UiButton variant="danger"> 危险操作 </UiButton>
        <UiButton variant="text"> 文字按钮 </UiButton>
        <UiButton variant="primary" loading> 提交中 </UiButton>
        <UiButton disabled> 禁用 </UiButton>
      </div>
    </UiCard>

    <UiCard class="section">
      <h2>表单字段</h2>
      <UiField label="用户名" hint="3–20 个字符" required>
        <input class="input" placeholder="student01" />
      </UiField>
      <UiField label="密码" error="密码错误，请重试">
        <input class="input" type="password" />
      </UiField>
    </UiCard>

    <UiCard class="section">
      <h2>状态徽章</h2>
      <div class="row">
        <StatusBadge v-for="status in statuses" :key="status" :status="status" />
      </div>
    </UiCard>

    <UiCard class="section">
      <h2>分页</h2>
      <UiPagination
        v-model:page="page"
        :total="137"
        :page-size="10"
        @change="(value) => (page = value)"
      />
      <p class="muted">当前页：{{ page }}</p>
    </UiCard>

    <UiCard class="section">
      <h2>骨架与空/错状态</h2>
      <UiSkeleton :lines="3" />
      <EmptyState title="暂无工单" description="提交的报修会显示在这里" />
      <ErrorState title="加载失败" message="网络连接异常，请稍后重试" />
    </UiCard>
  </div>
</template>

<style scoped>
.showcase {
  max-width: 960px;
  margin: 0 auto;
  padding: var(--cp-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.section h2 {
  font-size: 15px;
  margin: 0 0 var(--cp-space-3);
}

.row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cp-space-2);
  align-items: center;
}

.input {
  min-height: var(--cp-control-md);
  width: 260px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
}

.muted {
  color: var(--cp-muted);
  font-size: 13px;
}
</style>
