import { computed, onMounted, ref, type Ref } from 'vue'

import { callApi } from '@/api/client'

/** 通用资源列表装载：分页/加载/错误三态，刷新全部从后端重取。 */
export function useResourceList<T>(fetcher: (page: number, pageSize: number) => Promise<{ items: T[]; total: number }>, pageSize = 10) {
  const items: Ref<T[]> = ref([])
  const total = ref(0)
  const page = ref(1)
  const loading = ref(true)
  const failed = ref(false)

  async function load() {
    loading.value = true
    failed.value = false
    try {
      const result = await fetcher(page.value, pageSize)
      items.value = result.items
      total.value = result.total
    } catch {
      failed.value = true
    } finally {
      loading.value = false
    }
  }

  async function changePage(next: number) {
    page.value = next
    await load()
  }

  const isEmpty = computed(() => !loading.value && !failed.value && items.value.length === 0)

  onMounted(load)

  return { items, total, page, pageSize, loading, failed, isEmpty, load, changePage }
}

export { callApi }
