<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    inputId?: string
    hint?: string
    error?: string
    required?: boolean
  }>(),
  { required: false, inputId: undefined, hint: undefined, error: undefined },
)
</script>

<template>
  <div class="ui-field" :class="{ 'ui-field--error': !!error }">
    <label class="ui-field__label" :for="inputId">
      {{ label }}
      <span v-if="required" class="ui-field__required" aria-hidden="true">*</span>
    </label>
    <div class="ui-field__control">
      <slot />
    </div>
    <p v-if="error" class="ui-field__message ui-field__message--error" role="alert">{{ error }}</p>
    <p v-else-if="hint" class="ui-field__message">{{ hint }}</p>
  </div>
</template>

<style scoped>
.ui-field {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
}

.ui-field__label {
  font-size: 13px;
  font-weight: 500;
  color: var(--cp-ink);
}

.ui-field__required {
  color: var(--cp-primary);
  margin-left: 2px;
}

.ui-field__message {
  margin: 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.ui-field__message--error {
  color: var(--cp-error);
}

.ui-field--error :deep(input),
.ui-field--error :deep(select),
.ui-field--error :deep(textarea) {
  border-color: var(--cp-error);
}
</style>
