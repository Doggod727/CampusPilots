<script setup lang="ts">
</script>

<template>
  <div class="home">
    <h1>CampusPilot</h1>
    <p>学生生活一站式社区 AI 助手</p>
    <p class="hint">前端工程基线已就绪，模块功能开发中</p>
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: var(--cp-canvas);
  color: var(--cp-body);
  font-family: var(--cp-font-sans);
}

h1 {
  margin: 0;
  font-size: 40px;
  letter-spacing: -0.02em;
}

.hint {
  font-size: 14px;
  color: var(--cp-muted);
}
</style>
