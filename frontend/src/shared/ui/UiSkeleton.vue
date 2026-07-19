<script setup lang="ts">
withDefaults(defineProps<{ lines?: number; card?: boolean }>(), { lines: 3, card: false })
</script>

<template>
  <div v-if="card" class="ui-skeleton ui-skeleton--card" aria-hidden="true" />
  <div v-else class="ui-skeleton__lines" aria-hidden="true">
    <div
      v-for="index in lines"
      :key="index"
      class="ui-skeleton__line"
      :style="{ width: `${100 - (index - 1) * 12}%` }"
    />
  </div>
</template>

<style scoped>
.ui-skeleton--card {
  height: 120px;
  border-radius: var(--cp-radius-card);
  background: linear-gradient(
    90deg,
    var(--cp-hairline-soft) 25%,
    var(--cp-canvas-soft) 50%,
    var(--cp-hairline-soft) 75%
  );
  background-size: 200% 100%;
  animation: ui-skeleton-pulse 1.4s ease infinite;
}

.ui-skeleton__lines {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.ui-skeleton__line {
  height: 12px;
  border-radius: 6px;
  background: linear-gradient(
    90deg,
    var(--cp-hairline-soft) 25%,
    var(--cp-canvas-soft) 50%,
    var(--cp-hairline-soft) 75%
  );
  background-size: 200% 100%;
  animation: ui-skeleton-pulse 1.4s ease infinite;
}

@keyframes ui-skeleton-pulse {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
