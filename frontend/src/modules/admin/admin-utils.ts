import { ApiError } from '@/api/client'

export interface Failure {
  title: string
  message: string
}

/** 把 ApiError 映射为可读的中文反馈；codeMap 覆盖各模块的稳定错误码。 */
export function describeApiError(error: unknown, fallbackTitle: string, codeMap: Record<string, string> = {}): Failure {
  if (error instanceof ApiError) {
    const mapped = codeMap[error.code]
    if (mapped) {
      return { title: fallbackTitle, message: mapped }
    }
    if (error.status === 401) {
      return { title: '登录状态失效', message: '请重新登录后再试。' }
    }
    if (error.status === 403) {
      return { title: '权限不足', message: '当前账号没有执行该操作的权限。' }
    }
    if (error.status === 404) {
      return { title: fallbackTitle, message: '目标资源不存在或已被移除。' }
    }
    if (error.status === 409) {
      return { title: '操作冲突', message: '数据已被其他操作更新，请刷新后重试。' }
    }
    if (error.status === 422) {
      return { title: '输入无效', message: error.details[0]?.reason ?? '请检查输入内容。' }
    }
    if (error.status === 429) {
      return { title: '请求过于频繁', message: '已达操作上限，请稍后再试。' }
    }
    return { title: fallbackTitle, message: error.message || '服务暂不可用，请稍后重试。' }
  }
  return { title: fallbackTitle, message: '服务暂不可用，请稍后重试。' }
}

export function formatTime(value?: string | null): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '—'
  }
  return date.toLocaleString('zh-CN', { hour12: false })
}
