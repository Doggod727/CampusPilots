from app.core.errors import AppError


class AgentNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404, code="AGENT_NOT_FOUND", message="Agent 不存在"
        )


class AgentDisabled(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409, code="AGENT_DISABLED", message="Agent 当前不可用"
        )


class DuplicateAgentRegistration(RuntimeError):
    pass


class InvalidAgentInput(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=422,
            code="AGENT_INPUT_INVALID",
            message="Agent 输入无效",
        )
