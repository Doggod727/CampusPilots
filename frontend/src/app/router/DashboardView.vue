<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { callApi } from '@/api/client'
import { getDashboardMetrics } from '@/api/generated'
import { filteredNav } from '@/app/router/navigation'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { EmptyState, ErrorState, PageHeader, UiCard, UiSkeleton } from '@/shared/ui'

const auth = useAuthStore()
const router = useRouter()

const loading = ref(true)
const failed = ref(false)
const summary = ref<Record<string, number> | null>(null)

const isAdmin = computed(() => auth.hasPermission('dashboard:read'))

const SUMMARY_LABELS: Record<string, string> = {
  active_users: '活跃用户',
  chat_messages: '问答消息',
  work_orders: '工单',
  posts: '帖子',
  lost_found_items: '失物招领',
  moderation_pending: '待审核',
  llm_tokens: '模型 Tokens',
}

const entries = computed(() =>
  filteredNav((code) => auth.hasPermission(code))
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => router.hasRoute(item.name)),
    }))
    .filter((group) => group.items.length > 0),
)

async function load() {
  loading.value = true
  failed.value = false
  try {
    if (isAdmin.value) {
      const today = new Date()
      const from = new Date(today.getTime() - 30 * 24 * 3600 * 1000)
      const response = await callApi(() =>
        getDashboardMetrics({
          query: {
            from: from.toISOString().slice(0, 10),
            to: today.toISOString().slice(0, 10),
          },
        }),
      )
      summary.value = response.data.summary as Record<string, number>
    }
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="dashboard">
    <PageHeader title="概览" :subtitle="`欢迎回来，${auth.user?.display_name ?? auth.user?.username}`" />

    <UiSkeleton v-if="loading" :lines="4" />
    <ErrorState v-else-if="failed" title="指标加载失败" message="无法获取看板数据，请稍后重试" @retry="load" />

    <template v-else>
      <section v-if="isAdmin && summary" class="dashboard__metrics">
        <UiCard v-for="(label, key) in SUMMARY_LABELS" :key="key" class="metric" padding="md">
          <p class="metric__value">{{ summary[key] ?? 0 }}</p>
          <p class="metric__label">{{ label }}</p>
        </UiCard>
      </section>

      <section class="dashboard__entries">
        <UiCard v-for="group in entries" :key="group.title" class="entry" padding="md">
          <h2 class="entry__title">{{ group.title }}</h2>
          <ul class="entry__list">
            <li v-for="item in group.items" :key="item.name">
              <RouterLink :to="{ name: item.name }" class="entry__link">{{ item.title }} →</RouterLink>
            </li>
          </ul>
        </UiCard>
        <EmptyState v-if="entries.length === 0" title="暂无可用入口" description="当前账号还没有可访问的功能" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.dashboard__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--cp-space-3);
}

.metric__value {
  margin: 0;
  font-size: 28px;
  font-weight: 600;
  color: var(--cp-ink);
  letter-spacing: -0.02em;
}

.metric__label {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.dashboard__entries {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--cp-space-3);
}

.entry__title {
  margin: 0 0 var(--cp-space-2);
  font-size: 14px;
}

.entry__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
}

.entry__link {
  font-size: 13px;
}
</style>
