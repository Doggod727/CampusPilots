<script setup lang="ts">
import { computed, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    label?: string
    value: unknown
    depth?: number
  }>(),
  { label: '', depth: 0 },
)

/** 后端已脱敏；此处对疑似密钥字段做兜底掩码。 */
const SECRET_PATTERN = /(secret|token|password|credential|api[-_]?key|authorization|private[-_]?key)/i

const masked = computed(() => SECRET_PATTERN.test(props.label))
const container = computed(() => !masked.value && props.value !== null && typeof props.value === 'object')
const open = ref(props.depth < 1)

const entries = computed<Array<[string, unknown]>>(() => {
  if (!container.value) {
    return []
  }
  if (Array.isArray(props.value)) {
    return props.value.map((item, index) => [String(index), item] as [string, unknown])
  }
  return Object.entries(props.value as Record<string, unknown>)
})

const summary = computed(() =>
  Array.isArray(props.value) ? `数组 · ${entries.value.length} 项` : `对象 · ${entries.value.length} 字段`,
)

function primitiveText(value: unknown): string {
  if (value === null) {
    return 'null'
  }
  if (value === undefined) {
    return 'undefined'
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value) ?? '—'
}
</script>

<template>
  <div class="json-tree">
    <div v-if="container" class="json-tree__node">
      <button type="button" class="json-tree__toggle" :aria-expanded="open" @click="open = !open">
        <span class="json-tree__arrow" aria-hidden="true">{{ open ? '▾' : '▸' }}</span>
        <span class="json-tree__key">{{ label || '根' }}</span>
        <span class="json-tree__summary">{{ summary }}</span>
      </button>
      <div v-show="open" class="json-tree__children">
        <JsonTree v-for="[key, child] in entries" :key="key" :label="key" :value="child" :depth="depth + 1" />
      </div>
    </div>
    <p v-else class="json-tree__leaf">
      <span class="json-tree__key">{{ label }}</span>
      <span class="json-tree__value" :class="{ 'json-tree__value--masked': masked }">
        {{ masked ? '***（已掩码）' : primitiveText(value) }}
      </span>
    </p>
  </div>
</template>

<style scoped>
.json-tree {
  font-family: var(--cp-font-mono);
  font-size: 12px;
  line-height: 1.7;
}

.json-tree__toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-1);
  padding: 0;
  border: none;
  background: transparent;
  color: var(--cp-ink);
  font: inherit;
  cursor: pointer;
}

.json-tree__arrow {
  color: var(--cp-muted-soft);
}

.json-tree__key {
  color: var(--cp-info);
}

.json-tree__summary {
  color: var(--cp-muted-soft);
}

.json-tree__children {
  margin-left: var(--cp-space-3);
  padding-left: var(--cp-space-2);
  border-left: 1px solid var(--cp-hairline-soft);
}

.json-tree__leaf {
  margin: 0;
  display: flex;
  gap: var(--cp-space-2);
}

.json-tree__value {
  color: var(--cp-body);
  word-break: break-all;
}

.json-tree__value--masked {
  color: var(--cp-warning);
}
</style>
