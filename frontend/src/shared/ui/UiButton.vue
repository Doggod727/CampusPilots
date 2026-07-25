<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: 'primary' | 'default' | 'danger' | 'text'
    size?: 'sm' | 'md' | 'lg'
    disabled?: boolean
    loading?: boolean
    type?: 'button' | 'submit' | 'reset'
  }>(),
  { variant: 'default', size: 'md', type: 'button', disabled: false, loading: false },
)
</script>

<template>
  <button
    class="ui-button"
    :class="[`ui-button--${variant}`, `ui-button--${size}`]"
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <span v-if="loading" class="ui-button__spinner" aria-hidden="true" />
    <slot />
  </button>
</template>

<style scoped>
.ui-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--cp-space-2);
  border: 1px solid transparent;
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    color 0.15s ease;
  white-space: nowrap;
}

.ui-button--sm {
  min-height: var(--cp-control-sm);
  padding: 0 var(--cp-space-3);
  font-size: 13px;
}

.ui-button--md {
  min-height: var(--cp-control-md);
  padding: 0 var(--cp-space-4);
}

.ui-button--lg {
  min-height: var(--cp-control-lg);
  padding: 0 var(--cp-space-5);
  font-size: 15px;
}

.ui-button--primary {
  background: var(--cp-primary);
  color: var(--cp-on-primary);
}

.ui-button--primary:hover:not(:disabled) {
  background: var(--cp-primary-active);
}

.ui-button--default {
  background: var(--cp-surface-card);
  border-color: var(--cp-hairline-strong);
  color: var(--cp-ink);
}

.ui-button--default:hover:not(:disabled) {
  border-color: var(--cp-muted);
}

.ui-button--danger {
  background: var(--cp-error);
  color: var(--cp-on-primary);
}

.ui-button--danger:hover:not(:disabled) {
  filter: brightness(0.92);
}

.ui-button--text {
  background: transparent;
  color: var(--cp-primary);
  padding-inline: var(--cp-space-2);
}

.ui-button--text:hover:not(:disabled) {
  color: var(--cp-primary-active);
}

.ui-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.ui-button__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: ui-button-spin 0.7s linear infinite;
}

@keyframes ui-button-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
