<script setup lang="ts">
import { ref } from 'vue'

import { callApi } from '@/api/client'
import { getReadiness } from '@/api/generated'
import { UiButton, UiCard } from '@/shared/ui'

const checking = ref(false)
const emit = defineEmits<{ ready: [] }>()

async function retry() {
  checking.value = true
  try {
    const response = await callApi(() => getReadiness(), { retryOnUnauthorized: false })
    if (response.data.status === 'ready') {
      emit('ready')
    }
  } catch {
    // 保持离线页
  } finally {
    checking.value = false
  }
}
</script>

<template>
  <div class="offline">
    <UiCard padding="lg" class="offline__card">
      <h1 class="offline__title">服务暂不可用</h1>
      <p class="offline__message">无法连接 CampusPilot 后端或其依赖（数据库 / 缓存 / 向量库）。请确认服务已启动后重试。</p>
      <UiButton variant="primary" :loading="checking" @click="retry">重新检查</UiButton>
    </UiCard>
  </div>
</template>

<style scoped>
.offline {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--cp-space-5);
  background: var(--cp-canvas);
}

.offline__card {
  max-width: 440px;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.offline__title {
  margin: 0;
  font-size: 20px;
}

.offline__message {
  margin: 0;
  color: var(--cp-muted);
}
</style>
