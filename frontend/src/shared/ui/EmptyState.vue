<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    description?: string
  }>(),
  { title: '暂无数据', description: '' },
)
</script>

<template>
  <div class="empty-state">
    <div class="empty-state__icon" aria-hidden="true">
      <slot name="icon">—</slot>
    </div>
    <p class="empty-state__title">{{ title }}</p>
    <p v-if="description" class="empty-state__description">{{ description }}</p>
    <div v-if="$slots.default" class="empty-state__actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--cp-space-2);
  padding: var(--cp-space-8) var(--cp-space-5);
  text-align: center;
  color: var(--cp-muted);
}

.empty-state__icon {
  font-size: 28px;
  line-height: 1;
}

.empty-state__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--cp-ink);
}

.empty-state__description {
  margin: 0;
  font-size: 13px;
  max-width: 420px;
}

.empty-state__actions {
  margin-top: var(--cp-space-3);
}
</style>
