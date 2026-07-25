<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    message?: string
    retryText?: string
  }>(),
  { title: '加载失败', message: '', retryText: '重试' },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="error-state" role="alert">
    <p class="error-state__title">{{ title }}</p>
    <p v-if="message" class="error-state__message">{{ message }}</p>
    <slot>
      <button v-if="retryText" type="button" class="error-state__retry" @click="emit('retry')">
        {{ retryText }}
      </button>
    </slot>
  </div>
</template>

<style scoped>
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--cp-space-2);
  padding: var(--cp-space-8) var(--cp-space-5);
  text-align: center;
}

.error-state__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--cp-error);
}

.error-state__message {
  margin: 0;
  font-size: 13px;
  color: var(--cp-muted);
  max-width: 480px;
}

.error-state__retry {
  margin-top: var(--cp-space-3);
  min-height: var(--cp-control-md);
  padding: 0 var(--cp-space-4);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  color: var(--cp-ink);
  cursor: pointer;
}

.error-state__retry:hover {
  border-color: var(--cp-muted);
}
</style>
