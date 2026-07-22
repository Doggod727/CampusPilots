<script setup lang="ts">
import { useRouter } from 'vue-router'

import { UiButton } from '@/shared/ui'

const router = useRouter()
</script>

<template>
  <div class="forbidden">
    <p class="forbidden__code">403</p>
    <h1 class="forbidden__title">没有访问权限</h1>
    <p class="forbidden__message">当前账号无权查看该页面。如需访问，请联系管理员调整角色权限。</p>
    <UiButton variant="primary" @click="router.replace({ name: 'chat' })">返回首页</UiButton>
  </div>
</template>

<style scoped>
.forbidden {
  min-height: 70vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--cp-space-3);
  text-align: center;
  padding: var(--cp-space-5);
}

.forbidden__code {
  margin: 0;
  font-size: 56px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--cp-hairline-strong);
}

.forbidden__title {
  margin: 0;
  font-size: 20px;
}

.forbidden__message {
  margin: 0;
  color: var(--cp-muted);
  max-width: 420px;
}
</style>
