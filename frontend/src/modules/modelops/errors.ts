import { ApiError } from '@/api/client'

/** ModelOps 稳定错误码 → 中文说明（与后端 agent_platform 模块定义一致，不臆造语义）。 */
const STABLE_CODE_MESSAGES: Readonly<Record<string, string>> = {
  TOOL_STATE_CONFIRMATION_REQUIRED: 'Tool 状态变更需要明确确认。',
  DATASET_NOT_FOUND: '数据集不存在或已删除。',
  DATASET_IN_USE: '数据集正被活动训练任务引用，无法删除。',
  DATASET_VERSION_NOT_FOUND: '数据集版本不存在。',
  DATASET_VERSION_STATE_CONFLICT: '版本当前状态不允许该操作（仅校验通过、不含敏感数据且未冻结的版本可冻结）。',
  DATASET_ARTIFACT_TOO_LARGE: '文件超过大小限制（单文件最大 100 MiB）。',
  DATASET_ARTIFACT_UNSUPPORTED: '仅支持 JSONL 或 CSV 格式的数据文件。',
  DATASET_ARTIFACT_INVALID: '产物无效或已过期（可能哈希不一致），请重新上传。',
  DUPLICATE_RESOURCE: '同名资源已存在，请更换名称。',
  TRAINING_JOB_NOT_FOUND: '训练任务不存在。',
  TRAINING_DATASET_NOT_READY: '数据集版本尚未就绪：需已冻结、校验通过且不含敏感数据。',
  TRAINING_BASE_MODEL_NOT_ALLOWED: '基座模型不在允许清单中。',
  TRAINING_STATE_CONFLICT: '训练任务当前状态不允许该操作。',
  MODEL_NOT_FOUND: '模型版本不存在。',
  MODEL_EVALUATION_REQUIRED: '模型尚未通过评估，未成功评估的模型不允许激活。',
  MODEL_FALLBACK_REQUIRED: '复杂生成必须保留 DeepSeek 活动兜底模型。',
  MODEL_STATE_CONFLICT: '模型当前状态不允许该操作。',
  MODEL_ARTIFACT_INVALID: '模型产物无效。',
  EVALUATION_NOT_FOUND: '评估任务不存在。',
  EVALUATION_TARGET_NOT_FOUND: '评估目标不存在。',
  EVALUATION_DATASET_NOT_READY: '评估数据集版本尚未就绪。',
  EVALUATION_NOT_COMPLETED: '评估尚未成功完成，不能用于该操作。',
}

/** 将 API 错误转换为界面可读文案；优先稳定错误码，其次状态码兜底。 */
export function describeModelOpsError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const mapped = STABLE_CODE_MESSAGES[error.code]
    if (mapped) {
      return mapped
    }
    if (error.status === 403) {
      return '当前账号没有执行该操作的权限。'
    }
    if (error.status === 404) {
      return '目标资源不存在或当前账号无权访问。'
    }
    if (error.status === 422) {
      return error.details[0]?.reason ?? '提交内容未通过校验，请检查后重试。'
    }
    if (error.status === 429) {
      return '请求过于频繁，请稍后再试。'
    }
    if (error.message) {
      return error.message
    }
  }
  return fallback
}
