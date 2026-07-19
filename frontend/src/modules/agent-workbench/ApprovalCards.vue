<script setup lang="ts">
import { ref } from 'vue'

import { ApiError, callApi } from '@/api/client'
import { decideAgentToolApproval } from '@/api/generated'
import type { Approval } from '@/api/generated'
import { StatusBadge, UiButton, UiCard } from '@/shared/ui'

const props = defineProps<{
  runId: string
  approvals: Approval[]
}>()

const emit = defineEmits<{ decided: [] }>()

const deciding = ref<string | null>(null)
const notice = ref<{ kind: 'error' | 'info'; text: string } | null>(null)

const now = ref(Date.now())

function isExpired(approval: Approval): boolean {
  return new Date(approval.expires_at).getTime() <= now.value
}

function expiryText(approval: Approval): string {
  const ms = new Date(approval.expires_at).getTime() - now.value
  if (ms <= 0) return '已过期'
  const minutes = Math.floor(ms / 60000)
  return minutes >= 1 ? `${minutes} 分钟后过期` : '即将过期'
}

function decisionNotice(error: unknown): { kind: 'error' | 'info'; text: string } {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return { kind: 'info', text: '该审批已被处理（一次性消费，不可重复决定）。' }
    }
    if (error.status === 403) {
      return { kind: 'error', text: '没有审批该请求的权限。' }
    }
  }
  return { kind: 'error', text: '操作失败，请稍后重试。' }
}

async function decide(approval: Approval, decision: 'approve' | 'reject') {
  if (deciding.value) {
    return
  }
  deciding.value = approval.id
  notice.value = null
  try {
    await callApi(() =>
      decideAgentToolApproval({
        path: { run_id: props.runId, approval_id: approval.id },
        body: { decision, argument_hash: approval.argument_hash },
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      }),
    )
    notice.value = { kind: 'info', text: decision === 'approve' ? '已批准，运行将继续。' : '已拒绝，运行将安全结束。' }
    emit('decided')
  } catch (error) {
    notice.value = decisionNotice(error)
  } finally {
    deciding.value = null
  }
}

</script>

<template>
  <section class="approvals">
    <h2 class="approvals__heading">审批（{{ approvals.length }}）</h2>
    <UiCard v-for="approval in approvals" :key="approval.id" class="approvals__item" padding="md">
      <div class="approvals__head">
        <code>{{ approval.tool_name }}</code>
        <StatusBadge :status="approval.status" />
        <span class="approvals__expiry" :class="{ 'approvals__expiry--expired': isExpired(approval) && approval.status === 'pending' }">
          {{ approval.status === 'pending' ? expiryText(approval) : '' }}
        </span>
      </div>
      <p class="approvals__hash">参数哈希：{{ approval.argument_hash }}</p>
      <div v-if="approval.status === 'pending' && !isExpired(approval)" class="approvals__actions">
        <UiButton variant="primary" size="sm" :loading="deciding === approval.id" :disabled="!!deciding" @click="decide(approval, 'approve')">批准</UiButton>
        <UiButton variant="danger" size="sm" :disabled="!!deciding" @click="decide(approval, 'reject')">拒绝</UiButton>
      </div>
      <p v-else-if="approval.status === 'pending' && isExpired(approval)" class="approvals__expired-note">审批已过期，需重新发起。</p>
    </UiCard>
    <p v-if="notice" class="approvals__notice" :class="`approvals__notice--${notice.kind}`" role="status">{{ notice.text }}</p>
  </section>
</template>

<style scoped>
.approvals {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.approvals__heading {
  margin: 0 0 var(--cp-space-1);
  font-size: 15px;
}

.approvals__head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.approvals__expiry {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-warning);
}

.approvals__expiry--expired {
  color: var(--cp-error);
}

.approvals__hash {
  margin: var(--cp-space-2) 0 0;
  font-size: 11px;
  color: var(--cp-muted-soft);
  font-family: var(--cp-font-mono);
  word-break: break-all;
}

.approvals__actions {
  display: flex;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-3);
}

.approvals__expired-note {
  margin: var(--cp-space-2) 0 0;
  font-size: 12px;
  color: var(--cp-error);
}

.approvals__notice {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
}

.approvals__notice--info {
  color: var(--cp-info);
}

.approvals__notice--error {
  color: var(--cp-error);
}
</style>
