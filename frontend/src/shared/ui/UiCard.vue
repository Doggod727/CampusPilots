<script setup lang="ts">
withDefaults(defineProps<{ padding?: 'none' | 'sm' | 'md' | 'lg'; as?: string }>(), {
  padding: 'md',
  as: 'div',
})
</script>

<template>
  <component :is="as" class="ui-card" :class="`ui-card--${padding}`">
    <slot />
  </component>
</template>

<style scoped>
.ui-card {
  background: var(--cp-surface-card);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  box-shadow: none;
}

.ui-card--none {
  padding: 0;
}

.ui-card--sm {
  padding: var(--cp-space-3);
}

.ui-card--md {
  padding: var(--cp-space-5);
}

.ui-card--lg {
  padding: var(--cp-space-8);
}
</style>
