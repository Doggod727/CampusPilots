from app.core.errors import AppError


class WorkOrderNumberExhausted(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            code="WORK_ORDER_NUMBER_EXHAUSTED",
            message="当日工单编号已用尽，请稍后重试",
        )


class CampusNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="CAMPUS_NOT_FOUND",
            message="校区不存在或已停用",
        )


class WorkOrderNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="WORK_ORDER_NOT_FOUND",
            message="工单不存在或不可见",
        )


class WorkOrderIllegalTransition(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="WORK_ORDER_ILLEGAL_TRANSITION",
            message="工单状态不允许执行该流转",
        )


class ResourceVersionConflict(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="RESOURCE_VERSION_CONFLICT",
            message="工单版本已变化，请刷新后重试",
        )


class WorkOrderAlreadyRated(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="WORK_ORDER_ALREADY_RATED",
            message="工单已经评价",
        )


class WorkOrderNotCompleted(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="WORK_ORDER_NOT_COMPLETED",
            message="仅已完成工单可以评价",
        )


class WorkOrderApprovalInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="TOOL_APPROVAL_INVALID",
            message="工具确认信息无效",
        )
