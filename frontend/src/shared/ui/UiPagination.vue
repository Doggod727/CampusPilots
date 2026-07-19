<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    page: number
    total: number
    pageSize?: number
  }>(),
  { pageSize: 20 },
)

const emit = defineEmits<{ change: [page: number] }>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))

const pages = computed(() => {
  const current = props.page
  const last = totalPages.value
  const window = 2
  const start = Math.max(1, Math.min(current - window, last - window * 2))
  const end = Math.min(last, start + window * 2)
  const list: number[] = []
  for (let value = start; value <= end; value += 1) {
    list.push(value)
  }
  return list
})
</script>

<template>
  <nav v-if="totalPages > 1" class="ui-pagination" aria-label="分页">
    <button
      type="button"
      class="ui-pagination__nav"
      :disabled="page <= 1"
      @click="emit('change', page - 1)"
    >
      上一页
    </button>
    <button
      v-for="value in pages"
      :key="value"
      type="button"
      class="ui-pagination__page"
      :class="{ 'ui-pagination__page--active': value === page }"
      :aria-current="value === page ? 'page' : undefined"
      @click="emit('change', value)"
    >
      {{ value }}
    </button>
    <button
      type="button"
      class="ui-pagination__nav"
      :disabled="page >= totalPages"
      @click="emit('change', page + 1)"
    >
      下一页
    </button>
  </nav>
</template>

<style scoped>
.ui-pagination {
  display: flex;
  align-items: center;
  gap: var(--cp-space-1);
}

.ui-pagination__nav,
.ui-pagination__page {
  min-width: var(--cp-control-sm);
  min-height: var(--cp-control-sm);
  padding: 0 var(--cp-space-2);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.ui-pagination__nav:hover:not(:disabled),
.ui-pagination__page:hover {
  border-color: var(--cp-muted);
}

.ui-pagination__page--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.ui-pagination__nav:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
