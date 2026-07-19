export interface ApiErrorDetail {
  field?: string | null
  reason: string
}

/** 统一错误信封的领域错误：只携带稳定字段，绝不包含 Token 或请求正文。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: readonly ApiErrorDetail[]
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, details: readonly ApiErrorDetail[] = [], requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

interface ErrorEnvelope {
  code?: unknown
  message?: unknown
  details?: unknown
  request_id?: unknown
}

export function toApiError(status: number, body: unknown, requestId: string | null): ApiError {
  const envelope = (body ?? {}) as ErrorEnvelope
  const code = typeof envelope.code === 'string' && envelope.code ? envelope.code : 'HTTP_ERROR'
  const message =
    typeof envelope.message === 'string' && envelope.message ? envelope.message : `请求失败（${status}）`
  const details = Array.isArray(envelope.details)
    ? envelope.details
        .filter((item): item is { field?: string; reason: unknown } => typeof item === 'object' && item !== null)
        .map((item) => ({
          field: typeof item.field === 'string' ? item.field : null,
          reason: typeof item.reason === 'string' ? item.reason : '无效',
        }))
    : []
  const resolvedRequestId =
    typeof envelope.request_id === 'string' ? envelope.request_id : requestId
  return new ApiError(status, code, message, details, resolvedRequestId)
}

export const isUnauthorized = (error: ApiError) => error.status === 401
export const isForbidden = (error: ApiError) => error.status === 403
export const isConflict = (error: ApiError) => error.status === 409
export const isValidationError = (error: ApiError) => error.status === 422
export const isRateLimited = (error: ApiError) => error.status === 429
export const isProviderFailure = (error: ApiError) => error.status === 502 || error.status === 504
export const isServiceUnavailable = (error: ApiError) => error.status === 503
