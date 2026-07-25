<script setup lang="ts">
import { useRouter } from 'vue-router'

import { UiButton } from '@/shared/ui'

const router = useRouter()
</script>

<template>
  <div class="not-found">
    <p class="not-found__code">404</p>
    <h1 class="not-found__title">页面不存在</h1>
    <p class="not-found__message">访问的页面不存在或已被移动，请从导航重新进入。</p>
    <UiButton variant="primary" @click="router.replace({ name: 'dashboard' })">返回概览</UiButton>
  </div>
</template>

<style scoped>
.not-found {
  min-height: 70vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--cp-space-3);
  text-align: center;
}

.not-found__code {
  margin: 0;
  font-size: 56px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--cp-hairline-strong);
}

.not-found__title {
  margin: 0;
  font-size: 20px;
}

.not-found__message {
  margin: 0;
  color: var(--cp-muted);
}
</style>
