<script setup lang="ts">
import { computed, onBeforeUnmount, onUnmounted, reactive, ref } from 'vue'

import { ApiError, callApi } from '@/api/client'
import { revealAnonymousIdentity } from '@/api/generated'
import type { AnonymousIdentityReveal } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { EmptyState, PageHeader, UiButton, UiCard, UiField } from '@/shared/ui'

const auth = useAuthStore()
const allowed = computed(() => auth.hasPermission('community:anonymous_identity:read'))

const form = reactive({ target_type: 'post' as 'post' | 'comment', target_id: '', reason: '' })
const submitting = ref(false)
const failure = ref('')
/** 反查结果只驻留内存：不落盘、不写日志，离开页面即清空。 */
const result = ref<AnonymousIdentityReveal | null>(null)

const canSubmit = computed(
  () => form.target_id.trim().length > 0 && form.reason.trim().length > 0 && !submitting.value,
)

function clearReveal() {
  result.value = null
  form.reason = ''
}

onBeforeUnmount(clearReveal)
onUnmounted(clearReveal)

async function submit() {
  if (!canSubmit.value) {
    return
  }
  submitting.value = true
  failure.value = ''
  result.value = null
  try {
    const response = await callApi(() =>
      revealAnonymousIdentity({
        body: {
          target_type: form.target_type,
          target_id: form.target_id.trim(),
          reason: form.reason.trim(),
        },
      }),
    )
    result.value = response.data
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 403) {
        failure.value = '当前账号没有匿名身份反查权限'
      } else if (error.status === 404) {
        failure.value = '目标内容不存在或不可见'
      } else if (error.status === 422) {
        failure.value = error.details[0]?.reason ?? '输入内容不符合要求'
      } else if (error.status === 429) {
        failure.value = '操作过于频繁，请稍后再试'
      } else {
        failure.value = '服务暂不可用，请稍后重试'
      }
    } else {
      failure.value = '服务暂不可用，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="reveal">
    <PageHeader
      title="匿名身份反查"
      subtitle="仅限持有专项权限的账号使用；每次成功或失败的反查都会写入审计日志"
    />

    <EmptyState
      v-if="!allowed"
      title="权限不足"
      description="当前账号没有匿名身份反查权限（community:anonymous_identity:read）"
    />

    <template v-else>
      <UiCard class="reveal__card" padding="lg">
        <form class="reveal__form" @submit.prevent="submit">
          <UiField label="内容类型" input-id="reveal-type" required>
            <select id="reveal-type" v-model="form.target_type" class="reveal__input" :disabled="submitting">
              <option value="post">帖子</option>
              <option value="comment">评论</option>
            </select>
          </UiField>
          <UiField label="内容标识" input-id="reveal-target" required hint="目标帖子或评论的 ID">
            <input
              id="reveal-target"
              v-model="form.target_id"
              class="reveal__input"
              maxlength="64"
              placeholder="例如：9f1c…（内容 ID）"
              :disabled="submitting"
            />
          </UiField>
          <UiField label="反查事由" input-id="reveal-reason" required hint="必须填写明确、可审计的事由">
            <textarea
              id="reveal-reason"
              v-model="form.reason"
              class="reveal__input"
              rows="3"
              maxlength="500"
              placeholder="例如：该内容涉嫌人身攻击，处理举报案件需要联系作者核实"
              :disabled="submitting"
            />
          </UiField>
          <p v-if="failure" class="reveal__error" role="alert">{{ failure }}</p>
          <div class="reveal__actions">
            <UiButton variant="primary" type="submit" :loading="submitting" :disabled="!canSubmit">发起反查</UiButton>
          </div>
        </form>
      </UiCard>

      <UiCard v-if="result" class="reveal__result" padding="lg">
        <h2 class="reveal__result-title">反查结果（仅本次页面会话内展示，离开即清除）</h2>
        <dl class="reveal__result-grid">
          <dt>作者用户 ID</dt>
          <dd class="reveal__mono">{{ result.author_user_id }}</dd>
          <dt>用户名</dt>
          <dd>{{ result.username }}</dd>
          <dt>显示名</dt>
          <dd>{{ result.display_name }}</dd>
          <dt>目标内容</dt>
          <dd class="reveal__mono">{{ result.target_type === 'post' ? '帖子' : '评论' }} · {{ result.target_id }}</dd>
          <dt>反查时间</dt>
          <dd>{{ formatTime(result.revealed_at) }}</dd>
        </dl>
        <p class="reveal__audit" role="status">本次操作已记录审计</p>
      </UiCard>
    </template>
  </div>
</template>

<style scoped>
.reveal {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
  max-width: 720px;
}

.reveal__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.reveal__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  color: var(--cp-ink);
  background: var(--cp-surface-card);
  box-sizing: border-box;
}

textarea.reveal__input {
  resize: vertical;
}

.reveal__error {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.reveal__actions {
  display: flex;
  justify-content: flex-end;
}

.reveal__result-title {
  margin: 0 0 var(--cp-space-3);
  font-size: 15px;
  color: var(--cp-ink);
}

.reveal__result-grid {
  margin: 0;
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: var(--cp-space-2) var(--cp-space-4);
  font-size: 14px;
}

.reveal__result-grid dt {
  color: var(--cp-muted);
}

.reveal__result-grid dd {
  margin: 0;
  color: var(--cp-ink);
  word-break: break-all;
}

.reveal__mono {
  font-family: var(--cp-font-mono);
  font-size: 13px;
}

.reveal__audit {
  margin: var(--cp-space-4) 0 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-warning) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
  color: var(--cp-warning);
  font-size: 13px;
}
</style>
