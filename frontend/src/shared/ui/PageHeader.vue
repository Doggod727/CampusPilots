<script setup lang="ts">
defineProps<{
  title: string
  eyebrow?: string
  subtitle?: string
}>()
</script>

<template>
  <header class="page-header">
    <div class="page-header__text">
      <p v-if="eyebrow" class="page-header__eyebrow">{{ eyebrow }}</p>
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="subtitle" class="page-header__subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.default" class="page-header__actions">
      <slot />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--cp-space-4);
  flex-wrap: wrap;
  padding-block: var(--cp-space-5);
}

.page-header__eyebrow {
  margin: 0 0 var(--cp-space-1);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--cp-muted);
}

.page-header__title {
  margin: 0;
  font-size: 26px;
  font-weight: 600;
}

.page-header__subtitle {
  margin: var(--cp-space-1) 0 0;
  font-size: 14px;
  color: var(--cp-muted);
  max-width: 640px;
}

.page-header__actions {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}
</style>
