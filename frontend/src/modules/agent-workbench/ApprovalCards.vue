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

const TOOL_APPROVAL_FALLBACKS: Readonly<Record<string, string>> = {
  'electricity.create_topup_request': '是否允许 CampusPilot 为您提交电费充值申请？',
  'work_order.create': '是否允许 CampusPilot 为您提交报修工单？',
  'event.register': '是否允许 CampusPilot 为您报名该校园活动？',
  'lost_found.publish': '是否允许 CampusPilot 为您发布失物招领信息？',
}

function approvalQuestion(approval: Approval): string {
  const summary = approval.argument_summary.display_summary
  if (typeof summary === 'string' && summary.includes('CampusPilot')) return summary
  return TOOL_APPROVAL_FALLBACKS[approval.tool_name] ?? '是否允许 CampusPilot 执行这项操作？'
}

function approvalDisplayText(approval: Approval): string {
  const question = approvalQuestion(approval)
  if (approval.status === 'pending') return question
  if (approval.status === 'approved' || approval.status === 'consumed') {
    return question.replace(/^是否允许 CampusPilot/, '已允许 CampusPilot').replace(/？$/, '。')
  }
  if (approval.status === 'rejected') {
    return question.replace(/^是否允许 CampusPilot/, '已拒绝 CampusPilot').replace(/？$/, '。')
  }
  return `该授权请求已过期：${question}`
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
        <strong class="approvals__question">{{ approvalDisplayText(approval) }}</strong>
        <StatusBadge :status="approval.status" />
        <span class="approvals__expiry" :class="{ 'approvals__expiry--expired': isExpired(approval) && approval.status === 'pending' }">
          {{ approval.status === 'pending' ? expiryText(approval) : '' }}
        </span>
      </div>
      <div v-if="approval.status === 'pending' && !isExpired(approval)" class="approvals__actions">
        <UiButton variant="primary" size="sm" :loading="deciding === approval.id" :disabled="!!deciding" @click="decide(approval, 'approve')">允许</UiButton>
        <UiButton variant="danger" size="sm" :disabled="!!deciding" @click="decide(approval, 'reject')">暂不允许</UiButton>
      </div>
      <p v-else-if="approval.status === 'pending' && isExpired(approval)" class="approvals__expired-note">审批已过期，需重新发起。</p>
      <details class="approvals__technical">
        <summary>查看技术信息</summary>
        <code>{{ approval.tool_name }}</code>
        <p class="approvals__hash">参数校验码：{{ approval.argument_hash }}</p>
      </details>
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

.approvals__question {
  flex: 1;
  font-size: 14px;
  line-height: 1.6;
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

.approvals__technical {
  margin-top: var(--cp-space-2);
  color: var(--cp-muted);
  font-size: 12px;
}

.approvals__technical summary {
  cursor: pointer;
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
