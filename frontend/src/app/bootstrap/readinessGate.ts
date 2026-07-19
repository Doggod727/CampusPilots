import { createApp, h } from 'vue'

import { callApi } from '@/api/client'
import { getReadiness } from '@/api/generated'

import OfflineView from './OfflineView.vue'

/** 启动就绪闸门：ready 通过才挂载应用，否则展示可重试的离线页。 */
export async function mountWithReadinessGate(mountApp: () => void): Promise<void> {
  try {
    const response = await callApi(() => getReadiness(), { retryOnUnauthorized: false })
    if (response.data.status === 'ready') {
      mountApp()
      return
    }
  } catch {
    // 落入离线页
  }
  const offline = createApp({
    render: () => h(OfflineView, { onReady: () => window.location.reload() }),
  })
  offline.mount('#app')
}
