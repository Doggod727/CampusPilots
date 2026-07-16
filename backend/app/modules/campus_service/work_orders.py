from app.core.errors import AppError


class WorkOrderNumberExhausted(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            code="WORK_ORDER_NUMBER_EXHAUSTED",
            message="当日工单编号已用尽，请稍后重试",
        )
