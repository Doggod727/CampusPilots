import { ApiError } from '@/api/client'
import type {
  FaultCategory,
  StudentType,
  WorkOrderStatus,
  WorkOrderTransitionRequest,
} from '@/api/generated'

export type WorkOrderTransitionTarget = WorkOrderTransitionRequest['target_status']

export interface TransitionAction {
  target: WorkOrderTransitionTarget
  label: string
  /** 完成流转时后端强制要求非空 completion_note。 */
  requiresCompletionNote: boolean
  danger: boolean
}

/**
 * 当前状态下的合法流转动作，与后端 work_order_state.py 状态机矩阵保持一致：
 * submitted → accepted/rejected/cancelled；accepted → processing；processing → completed。
 */
export function legalTransitions(status: WorkOrderStatus): TransitionAction[] {
  switch (status) {
    case 'submitted':
      return [
        { target: 'accepted', label: '受理', requiresCompletionNote: false, danger: false },
        { target: 'rejected', label: '驳回', requiresCompletionNote: false, danger: true },
        { target: 'cancelled', label: '取消工单', requiresCompletionNote: false, danger: true },
      ]
    case 'accepted':
      return [{ target: 'processing', label: '开始处理', requiresCompletionNote: false, danger: false }]
    case 'processing':
      return [{ target: 'completed', label: '完成工单', requiresCompletionNote: true, danger: false }]
    default:
      return []
  }
}

export const WORK_ORDER_STATUS_LABELS: Record<WorkOrderStatus, string> = {
  submitted: '待受理',
  accepted: '已受理',
  processing: '处理中',
  completed: '已完成',
  cancelled: '已取消',
  rejected: '已驳回',
}

export const FAULT_CATEGORY_LABELS: Record<FaultCategory, string> = {
  electric: '电路维修',
  plumbing: '水路维修',
  network: '网络故障',
  furniture: '家具设施',
  door_window: '门窗维修',
  other: '其他',
}

export const STUDENT_TYPE_LABELS: Record<StudentType, string> = {
  undergraduate: '本科生',
  postgraduate: '研究生',
  international: '留学生',
  all: '全部学生',
}

function validationReason(error: ApiError, fallback: string): string {
  return error.details[0]?.reason ?? fallback
}

/** createWorkOrder 错误映射（契约稳定错误码）。 */
export function describeCreateError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '当前账号没有创建工单的权限'
    }
    if (error.status === 404) {
      return '所选校区不存在或已停用，请刷新后重试'
    }
    if (error.status === 409) {
      return error.code === 'IDEMPOTENCY_CONFLICT'
        ? '相同请求正在处理中，请勿重复提交'
        : '创建冲突，请稍后重试'
    }
    if (error.status === 422) {
      return validationReason(error, '输入内容不符合要求')
    }
    if (error.status === 429) {
      return '操作过于频繁，请稍后再试'
    }
    if (error.status === 503) {
      return '服务暂不可用，请稍后重试'
    }
  }
  return '创建失败，请稍后重试'
}

/** transitionWorkOrder 错误映射（乐观锁/状态机冲突走 409 稳定码）。 */
export function describeTransitionError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '当前账号无权执行该流转'
    }
    if (error.status === 404) {
      return '工单不存在或不在你的可见范围'
    }
    if (error.status === 409) {
      if (error.code === 'RESOURCE_VERSION_CONFLICT') {
        return '工单刚被他人更新，请刷新详情后重试'
      }
      if (error.code === 'WORK_ORDER_ILLEGAL_TRANSITION') {
        return '当前状态不允许执行该流转'
      }
      if (error.code === 'IDEMPOTENCY_CONFLICT') {
        return '相同请求正在处理中，请勿重复提交'
      }
      return '操作冲突，请刷新后重试'
    }
    if (error.status === 422) {
      return validationReason(error, '输入内容不符合要求')
    }
    if (error.status === 429) {
      return '操作过于频繁，请稍后再试'
    }
  }
  return '操作失败，请稍后重试'
}

/** rateWorkOrder 错误映射（重复评价/未完成评价均为 409 稳定码）。 */
export function describeRatingError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '只有工单创建者可以评价'
    }
    if (error.status === 404) {
      return '工单不存在或不可见'
    }
    if (error.status === 409) {
      if (error.code === 'WORK_ORDER_ALREADY_RATED') {
        return '该工单已经评价过，不能重复评价'
      }
      if (error.code === 'WORK_ORDER_NOT_COMPLETED') {
        return '工单完成后才能评价'
      }
      if (error.code === 'IDEMPOTENCY_CONFLICT') {
        return '相同请求正在处理中，请勿重复提交'
      }
      return '操作冲突，请刷新后重试'
    }
    if (error.status === 422) {
      return validationReason(error, '输入内容不符合要求')
    }
    if (error.status === 429) {
      return '操作过于频繁，请稍后再试'
    }
  }
  return '评价提交失败，请稍后重试'
}

/** createElectricityTopupRequest 错误映射。 */
export function describeTopupError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '当前账号没有充值权限，或该房间未绑定到你的账号'
    }
    if (error.status === 404) {
      return '房间不存在或未绑定到你的账号'
    }
    if (error.status === 409) {
      if (error.code === 'IDEMPOTENCY_CONFLICT') {
        return '相同请求正在处理中，请勿重复提交'
      }
      if (error.code === 'TOOL_APPROVAL_INVALID') {
        return '该充值申请缺少有效确认，已被拒绝'
      }
      return '操作冲突，请稍后重试'
    }
    if (error.status === 422) {
      return validationReason(error, '充值金额不符合要求（1.00–500.00 元）')
    }
    if (error.status === 429) {
      return '操作过于频繁，请稍后再试'
    }
  }
  return '充值申请失败，请稍后重试'
}

/** queryExternalServiceProgress 错误映射（超时/无记录/服务关闭）。 */
export function describeProgressError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) {
      return '未查询到该业务号的办理记录'
    }
    if (error.status === 503) {
      if (error.code === 'CAMPUS_SYSTEM_TIMEOUT') {
        return '校园系统查询超时，请稍后重试'
      }
      if (error.code === 'CAMPUS_SYSTEM_INVALID_RESPONSE') {
        return '校园系统返回了无效数据'
      }
      return '校园系统暂不可用或服务已关闭'
    }
    if (error.status === 422) {
      return validationReason(error, '输入内容不符合要求')
    }
    if (error.status === 429) {
      return '操作过于频繁，请稍后再试'
    }
  }
  return '查询失败，请稍后重试'
}

export function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
