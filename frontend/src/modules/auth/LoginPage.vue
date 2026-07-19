<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { UiButton, UiCard, UiField } from '@/shared/ui'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const form = reactive({ username: '', password: '' })
const submitting = ref(false)
const failure = ref<{ title: string; message: string; retryAfter?: number } | null>(null)

const canSubmit = computed(() => form.username.trim().length >= 2 && form.password.length >= 1 && !submitting.value)

function describeError(error: unknown): { title: string; message: string; retryAfter?: number } {
  if (error instanceof ApiError) {
    if (error.status === 423 || error.code === 'ACCOUNT_LOCKED') {
      return { title: '账号已锁定', message: '失败次数过多，请稍后再试。' }
    }
    if (error.code === 'ACCOUNT_DISABLED') {
      return { title: '账号已禁用', message: '该账号已被禁用，请联系管理员。' }
    }
    if (error.status === 401 || error.code === 'INVALID_CREDENTIALS') {
      return { title: '登录失败', message: '用户名或密码不正确。' }
    }
    if (error.status === 422) {
      return { title: '输入无效', message: error.details[0]?.reason ?? '请检查输入格式。' }
    }
    if (error.status === 429) {
      return { title: '请求过于频繁', message: '请稍后再试。' }
    }
  }
  return { title: '登录失败', message: '服务暂不可用，请稍后重试。' }
}

async function submit() {
  if (!canSubmit.value) {
    return
  }
  submitting.value = true
  failure.value = null
  try {
    await auth.login(form.username.trim(), form.password)
    form.password = ''
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect.startsWith('/') ? redirect : '/')
  } catch (error) {
    form.password = ''
    failure.value = describeError(error)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login">
    <UiCard class="login__card" padding="lg">
      <h1 class="login__title">登录 CampusPilot</h1>
      <p class="login__subtitle">学生生活一站式社区 AI 助手</p>

      <form class="login__form" @submit.prevent="submit">
        <UiField label="用户名" input-id="login-username" required>
          <input
            id="login-username"
            v-model="form.username"
            class="input"
            type="text"
            autocomplete="username"
            placeholder="student01"
            :disabled="submitting"
          />
        </UiField>
        <UiField label="密码" input-id="login-password" required>
          <input
            id="login-password"
            v-model="form.password"
            class="input"
            type="password"
            autocomplete="current-password"
            :disabled="submitting"
          />
        </UiField>

        <p v-if="failure" class="login__error" role="alert">
          <strong>{{ failure.title }}</strong>
          <span>{{ failure.message }}</span>
        </p>

        <UiButton variant="primary" size="lg" type="submit" :loading="submitting" :disabled="!canSubmit">
          登 录
        </UiButton>
      </form>
    </UiCard>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--cp-space-5);
  background: var(--cp-canvas);
}

.login__card {
  width: min(420px, 100%);
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.login__title {
  margin: 0;
  font-size: 22px;
}

.login__subtitle {
  margin: 0 0 var(--cp-space-2);
  color: var(--cp-muted);
  font-size: 13px;
}

.login__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.input {
  min-height: var(--cp-control-md);
  width: 100%;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  background: var(--cp-surface-card);
  color: var(--cp-ink);
}

.input:focus-visible {
  outline: 2px solid var(--cp-primary);
  outline-offset: 1px;
}

.login__error {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}
</style>
