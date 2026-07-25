<script setup lang="ts">
import { computed, onMounted } from 'vue'

import { useAgentCatalogStore } from '@/modules/agent-workbench/stores/catalog'
import { EmptyState, ErrorState, PageHeader, StatusBadge, UiCard, UiSkeleton } from '@/shared/ui'

const catalog = useAgentCatalogStore()
onMounted(() => catalog.load())

const RISK_LABEL: Record<string, string> = {
  r0: 'R0 只读',
  r1: 'R1 低风险',
  r2: 'R2 需审批',
  r3: 'R3 高风险',
}

const agents = computed(() => catalog.agents)
const tools = computed(() => catalog.tools)
</script>

<template>
  <div class="catalog">
    <PageHeader title="Agent 目录" subtitle="后端目录提供的 Agent 能力与 Tool 清单（含风险等级与启用状态）" />

    <UiSkeleton v-if="catalog.loading && !catalog.loaded" :lines="5" />
    <ErrorState v-else-if="catalog.failed" title="目录加载失败" @retry="catalog.load(true)" />

    <template v-else>
      <section class="catalog__section">
        <h2 class="catalog__heading">Agents（{{ agents.length }}）</h2>
        <EmptyState v-if="agents.length === 0" title="暂无可用 Agent" />
        <div class="catalog__grid">
          <UiCard v-for="agent in agents" :key="agent.code" class="catalog__item" padding="md">
            <div class="catalog__item-head">
              <strong>{{ agent.name }}</strong>
              <StatusBadge :status="agent.enabled ? 'active' : 'inactive'" :label="agent.enabled ? '启用' : '停用'" />
            </div>
            <p class="catalog__desc">{{ agent.description }}</p>
            <p class="catalog__meta">
              <code>{{ agent.code }}</code> · v{{ agent.version }} · 可用 Tool {{ agent.tool_allowlist.length }} 个
            </p>
            <div class="catalog__tools">
              <code v-for="tool in agent.tool_allowlist" :key="tool" class="catalog__tool">{{ tool }}</code>
            </div>
          </UiCard>
        </div>
      </section>

      <section class="catalog__section">
        <h2 class="catalog__heading">Tools（{{ tools.length }}）</h2>
        <EmptyState v-if="tools.length === 0" title="暂无 Tool" />
        <div class="catalog__grid">
          <UiCard v-for="tool in tools" :key="tool.name" class="catalog__item" padding="md">
            <div class="catalog__item-head">
              <strong>{{ tool.name }}</strong>
              <StatusBadge :status="tool.enabled ? 'active' : 'inactive'" :label="tool.enabled ? '启用' : '停用'" />
            </div>
            <p class="catalog__desc">{{ tool.description }}</p>
            <p class="catalog__meta">
              <span class="catalog__risk" :class="`catalog__risk--${tool.risk_level}`">{{ RISK_LABEL[tool.risk_level] ?? tool.risk_level }}</span>
              · {{ tool.module }} · v{{ tool.version }} · {{ tool.timeout_ms }}ms
            </p>
          </UiCard>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.catalog {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.catalog__heading {
  margin: 0 0 var(--cp-space-2);
  font-size: 15px;
}

.catalog__section {
  display: flex;
  flex-direction: column;
}

.catalog__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--cp-space-3);
}

.catalog__item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-2);
}

.catalog__desc {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-body);
  font-size: 13px;
}

.catalog__meta {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.catalog__tools {
  margin-top: var(--cp-space-2);
  display: flex;
  flex-wrap: wrap;
  gap: var(--cp-space-1);
}

.catalog__tool {
  font-size: 11px;
  padding: 2px 6px;
  border: 1px solid var(--cp-hairline);
  border-radius: 6px;
  background: var(--cp-canvas-soft);
}

.catalog__risk--r0 {
  color: var(--cp-success);
}

.catalog__risk--r1 {
  color: var(--cp-info);
}

.catalog__risk--r2 {
  color: var(--cp-warning);
}

.catalog__risk--r3 {
  color: var(--cp-error);
}
</style>
