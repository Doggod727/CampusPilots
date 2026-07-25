import { onMounted, ref } from 'vue'

import { callApi } from '@/api/client'
import { listDepartmentContacts } from '@/api/generated'

/**
 * 校区选项字典。契约暂无校区列表端点，从后端部门联系人（字典数据）聚合唯一 campus_code；
 * 不硬编码任何校区。加载失败时可重试，调用方决定降级或报错。
 */
export function useCampusOptions() {
  const options = ref<string[]>([])
  const loaded = ref(false)
  const failed = ref(false)

  async function load() {
    failed.value = false
    try {
      const response = await callApi(() => listDepartmentContacts({}))
      const codes = new Set<string>()
      for (const contact of response.data.items) {
        codes.add(contact.campus_code)
      }
      options.value = [...codes].sort()
      loaded.value = true
    } catch {
      failed.value = true
    }
  }

  onMounted(load)

  return { options, loaded, failed, load }
}
