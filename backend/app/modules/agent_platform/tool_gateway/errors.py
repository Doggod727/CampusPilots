from app.core.errors import AppError
from app.shared.responses import ErrorDetail


_FIELD_LABELS = {
    "room_id": "房间ID",
    "fault_type": "故障类型",
    "description": "描述",
    "available_time": "可上门时间",
    "attachments": "附件",
    "amount_cny": "充值金额",
    "event_id": "活动ID",
    "title": "标题",
    "category": "类别",
    "location": "地点",
    "starts_at": "开始时间",
    "ends_at": "结束时间",
    "registration_deadline": "报名截止时间",
    "capacity": "名额",
    "topic": "社区话题",
    "content": "正文",
    "item_type": "失物类型",
    "occurred_at": "发生时间",
    "query": "查询关键词",
    "limit": "查询条数",
}
_LITERAL_CHOICES = {
    "fault_type": "电气（electric）、水暖（plumbing）、网络（network）、家具（furniture）、门窗（door_window）或其他（other）",
    "category": "讲座、社团、体育、艺术、志愿、竞赛、就业或其他类别",
    "topic": "校园生活（campus-life）、互助（mutual-help）或树洞（tree-hole）",
    "item_type": "丢失（lost）或拾到（found）",
}


def _validation_reason(error: dict[str, object]) -> tuple[str, str]:
    location = error.get("loc") or ()
    field = ".".join(str(part) for part in location) or "参数"
    label = _FIELD_LABELS.get(field, field)
    error_type = str(error.get("type") or "")
    context = error.get("ctx") if isinstance(error.get("ctx"), dict) else {}
    if error_type == "missing":
        return field, f"缺少必填项“{label}”"
    if error_type in {"uuid_parsing", "uuid_type"}:
        return field, f"{label}必须是有效的 UUID"
    if error_type == "literal_error":
        expected = _LITERAL_CHOICES.get(field, str(context.get("expected") or "规定选项"))
        return field, f"{label}只能填写 {expected}"
    if error_type == "string_too_short":
        return field, f"{label}至少需要 {context.get('min_length')} 个字符"
    if error_type == "string_too_long":
        return field, f"{label}不能超过 {context.get('max_length')} 个字符"
    if error_type in {"datetime_from_date_parsing", "datetime_parsing", "datetime_type"}:
        return field, f"{label}必须是有效的日期时间"
    if error_type in {"greater_than_equal", "greater_than"}:
        boundary = context.get("ge", context.get("gt"))
        return field, f"{label}必须大于或等于 {boundary}"
    if error_type in {"less_than_equal", "less_than"}:
        boundary = context.get("le", context.get("lt"))
        return field, f"{label}必须小于或等于 {boundary}"
    if error_type == "extra_forbidden":
        return field, f"{label}不是该工具支持的参数"
    message = str(error.get("msg") or "格式不正确")
    return field, f"{label}格式不正确（{message}）"


class ToolNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="TOOL_NOT_FOUND", message="工具不存在")


class ToolDisabled(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOOL_DISABLED", message="工具已停用")


class ToolForbidden(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=403, code="TOOL_FORBIDDEN", message="无权调用该工具")


class ToolArgumentInvalid(AppError):
    def __init__(
        self,
        message: str = "工具参数无效",
        *,
        field: str | None = None,
        reason: str | None = None,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        issue_details = details
        if issue_details is None and reason is not None:
            issue_details = [ErrorDetail(field=field, reason=reason)]
        super().__init__(
            status_code=422,
            code="TOOL_ARGUMENT_INVALID",
            message=message,
            details=issue_details,
        )

    @classmethod
    def from_validation_errors(cls, errors: list[dict[str, object]]) -> "ToolArgumentInvalid":
        issues = [_validation_reason(error) for error in errors]
        details = [ErrorDetail(field=field, reason=reason) for field, reason in issues]
        message = "；".join(reason for _, reason in issues[:3]) or "工具参数无效"
        return cls(message, details=details)


class ToolApprovalRequired(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOOL_APPROVAL_REQUIRED", message="工具调用需要确认")


class ToolApprovalInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOOL_APPROVAL_INVALID", message="工具确认无效")


class ToolTimeout(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=504, code="TOOL_TIMEOUT", message="工具调用超时")


class ToolDependencyUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=502, code="TOOL_DEPENDENCY_UNAVAILABLE", message="工具依赖暂不可用")


class DuplicateToolRegistration(ValueError):
    pass
