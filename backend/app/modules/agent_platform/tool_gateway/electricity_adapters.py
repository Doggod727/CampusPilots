from app.modules.agent_platform.domain.contracts import ToolInvocationContext
from app.modules.agent_platform.tool_gateway.catalog import (
    ElectricityBalanceInput,
    ElectricityBalanceOutput,
    ElectricityTopupInput,
    ElectricityTopupOutput,
    ToolModel,
)
from app.modules.agent_platform.tool_gateway.errors import (
    ToolApprovalInvalid,
    ToolArgumentInvalid,
)
from app.modules.campus_service.electricity import ElectricityService


class ElectricityBalanceToolHandler:
    """Thin M5-to-M2 adapter; M2 retains resource-scope enforcement."""

    def __init__(self, service: ElectricityService) -> None:
        self._service = service

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> ElectricityBalanceOutput:
        data = ElectricityBalanceInput.model_validate(payload)
        result = await self._service.get_balance(
            user_id=invocation.user.user_id,
            room_ids=invocation.user.room_ids,
            room_id=data.room_id,
        )
        return ElectricityBalanceOutput.model_validate(
            {
                "room_id": result.room_id,
                "balance": result.balance,
                "currency": result.currency,
                "updated_at": result.updated_at,
                "source": result.source,
                "is_simulated": result.is_simulated,
            }
        )


class ElectricityTopupToolHandler:
    """Translate trusted approval/idempotency facts into the M2 top-up service."""

    def __init__(self, service: ElectricityService) -> None:
        self._service = service

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> ElectricityTopupOutput:
        if invocation.idempotency_key is None:
            raise ToolArgumentInvalid()
        if invocation.approval_id is None or not invocation.approval_verified:
            raise ToolApprovalInvalid()
        data = ElectricityTopupInput.model_validate(payload)
        result = await self._service.create_topup_request(
            user_id=invocation.user.user_id,
            room_ids=invocation.user.room_ids,
            room_id=data.room_id,
            amount=data.amount,
            idempotency_key=invocation.idempotency_key,
            agent_run_id=invocation.agent_run_id,
            approval_id=invocation.approval_id,
            approval_verified=invocation.approval_verified,
        )
        return ElectricityTopupOutput.model_validate(
            {
                "topup_request_id": result.request_id,
                "status": "simulated",
                "amount": result.amount,
                "notice": "模拟申请，不产生真实扣款或到账",
            }
        )
