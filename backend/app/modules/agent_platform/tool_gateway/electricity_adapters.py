from uuid import UUID

from app.modules.agent_platform.domain.contracts import ToolInvocationContext
from app.modules.agent_platform.tool_gateway.catalog import (
    ElectricityBalanceInput,
    ElectricityBalanceOutput,
    ElectricityTopupInput,
    ElectricityTopupOutput,
    RoomRefInput,
    ToolModel,
)
from app.modules.agent_platform.tool_gateway.errors import (
    ToolApprovalInvalid,
    ToolArgumentInvalid,
)
from app.modules.campus_service.electricity import ElectricityService


async def _resolve_room(
    service: ElectricityService,
    invocation: ToolInvocationContext,
    data: RoomRefInput,
) -> tuple[UUID, tuple[UUID, ...]]:
    """优先使用本人已绑定的 room_id；否则按自然语言地址解析/供应模拟账户。"""

    if data.room_id is not None:
        return data.room_id, invocation.user.room_ids
    account = await service.resolve_or_provision_account(
        user_id=invocation.user.user_id,
        campus=data.campus or "",
        dormitory_area=data.dormitory_area or "",
        building=data.building or "",
        room=data.room or "",
    )
    return account.room_id, (*invocation.user.room_ids, account.room_id)


class ElectricityBalanceToolHandler:
    """Thin M5-to-M2 adapter; M2 retains resource-scope enforcement."""

    def __init__(self, service: ElectricityService) -> None:
        self._service = service

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> ElectricityBalanceOutput:
        data = ElectricityBalanceInput.model_validate(payload)
        room_id, room_ids = await _resolve_room(self._service, invocation, data)
        result = await self._service.get_balance(
            user_id=invocation.user.user_id,
            room_ids=room_ids,
            room_id=room_id,
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
        room_id, room_ids = await _resolve_room(self._service, invocation, data)
        result = await self._service.create_topup_request(
            user_id=invocation.user.user_id,
            room_ids=room_ids,
            room_id=room_id,
            amount=data.amount_cny,
            idempotency_key=invocation.idempotency_key,
            agent_run_id=invocation.agent_run_id,
            approval_id=invocation.approval_id,
            approval_verified=invocation.approval_verified,
        )
        return ElectricityTopupOutput.model_validate(
            {
                "topup_request_id": result.request_id,
                "status": "credited",
                "amount": result.amount,
                "balance_after": result.balance_after,
                "notice": "充值已到账，余额已更新",
            }
        )
