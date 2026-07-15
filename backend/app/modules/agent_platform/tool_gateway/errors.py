from app.core.errors import AppError


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
    def __init__(self) -> None:
        super().__init__(status_code=422, code="TOOL_ARGUMENT_INVALID", message="工具参数无效")


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
