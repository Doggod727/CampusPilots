from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.modules.agent_platform.domain.contracts import ToolInvocationContext, UserContext
from app.modules.agent_platform.tool_gateway.catalog import (
    GuideItem,
    ServiceGuideInput,
    ServiceGuideOutput,
    ToolModel,
    WorkOrderCreateInput,
    WorkOrderCreateOutput,
    WorkOrderEvent,
    WorkOrderGetInput,
    WorkOrderGetOutput,
)
from app.modules.agent_platform.tool_gateway.errors import (
    ToolApprovalInvalid,
    ToolArgumentInvalid,
    ToolDependencyUnavailable,
    ToolForbidden,
)
from app.modules.campus_service.guides import ServiceGuideService
from app.modules.campus_service.work_order_errors import WorkOrderNotFound
from app.modules.campus_service.work_orders import WorkOrderService

FAULT_TYPES = {
    "electric": "electric",
    "electricity": "electric",
    "power": "electric",
    "plumbing": "plumbing",
    "water": "plumbing",
    "network": "network",
    "furniture": "furniture",
    "door": "door_window",
    "window": "door_window",
    "door_window": "door_window",
    "other": "other",
}
STATUS_TEXT = {
    "submitted": "工单已提交",
    "accepted": "工单已受理",
    "processing": "工单处理中",
    "completed": "工单已完成",
    "cancelled": "工单已取消",
    "rejected": "工单已拒绝",
}


class ServiceGuideToolHandler:
    def __init__(self, service: ServiceGuideService) -> None:
        self._service = service

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> ServiceGuideOutput:
        data = ServiceGuideInput.model_validate(payload)
        try:
            page = await self._service.search(
                page=1,
                page_size=10,
                q=data.query.strip(),
                campus_code=data.campus_id,
                student_type=data.student_type,
            )
        except ValueError as exc:
            raise ToolArgumentInvalid() from exc
        return ServiceGuideOutput(
            items=tuple(
                GuideItem(
                    guide_id=item.id,
                    title=item.title,
                    summary=item.summary,
                    location=item.location,
                    updated_at=item.updated_at,
                    steps=(),
                )
                for item in page.items
            )
        )


class WorkOrderCreateToolHandler:
    def __init__(
        self,
        service: WorkOrderService,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._now = now or (lambda: datetime.now(UTC))

    @staticmethod
    def _validate_payload(payload: ToolModel) -> WorkOrderCreateInput:
        try:
            return WorkOrderCreateInput.model_validate(payload)
        except ValidationError as exc:
            raise ToolArgumentInvalid.from_validation_errors(exc.errors()) from exc

    async def preflight(self, context: UserContext, payload: ToolModel) -> None:
        data = self._validate_payload(payload)
        if FAULT_TYPES.get(data.fault_type.strip().lower()) is None:
            raise ToolArgumentInvalid(
                "故障类型不受支持",
                field="fault_type",
                reason="故障类型请选择 electric、plumbing、network、furniture、door_window 或 other",
            )
        self._time_window(data.available_time)

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> WorkOrderCreateOutput:
        if invocation.idempotency_key is None:
            raise ToolArgumentInvalid("缺少幂等键", reason="系统未生成幂等键，请重新提交")
        if invocation.approval_id is None or not invocation.approval_verified:
            raise ToolApprovalInvalid()
        data = self._validate_payload(payload)
        fault_category = FAULT_TYPES.get(data.fault_type.strip().lower())
        if (
            fault_category is None
            or len(data.description) > 1000
            or data.attachments
        ):
            raise ToolArgumentInvalid(
                "工单参数不符合要求",
                reason="故障类型、描述或附件不符合工单要求；当前暂不支持附件",
            )
        preferred_start_at, preferred_end_at = self._time_window(data.available_time)
        result = await self._service.create_from_room_in_transaction(
            actor=invocation.user,
            room_ids=invocation.user.room_ids,
            room_id=data.room_id,
            fault_category=fault_category,
            description=data.description,
            preferred_start_at=preferred_start_at,
            preferred_end_at=preferred_end_at,
            idempotency_key=invocation.idempotency_key,
            request_id=invocation.user.request_id,
            agent_run_id=invocation.agent_run_id,
            approval_id=invocation.approval_id,
            approval_verified=invocation.approval_verified,
        )
        body = result.body["data"]
        return WorkOrderCreateOutput.model_validate(
            {
                "work_order_id": body["id"],
                "status": body["status"],
                "created_at": body["created_at"],
            }
        )

    def _time_window(self, value: str | None) -> tuple[datetime, datetime]:
        if value is None:
            now = self._now()
            if now.tzinfo is None:
                raise ToolArgumentInvalid("服务时钟缺少时区", field="available_time", reason="系统时间配置无效")
            return now.astimezone(UTC) + timedelta(days=1), now.astimezone(UTC) + timedelta(days=1, hours=2)
        parts = value.split("/")
        if len(parts) != 2:
            raise ToolArgumentInvalid("可上门时间格式无效", field="available_time", reason="可上门时间须为‘ISO开始时间/ISO结束时间’")
        try:
            start = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
            end = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
        except ValueError:
            raise ToolArgumentInvalid("可上门时间格式无效", field="available_time", reason="可上门时间包含无效日期") from None
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ToolArgumentInvalid("可上门时间范围无效", field="available_time", reason="时间必须包含时区，且结束时间晚于开始时间")
        return start.astimezone(UTC), end.astimezone(UTC)


class WorkOrderGetToolHandler:
    def __init__(self, service: WorkOrderService) -> None:
        self._service = service

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> WorkOrderGetOutput:
        data = WorkOrderGetInput.model_validate(payload)
        try:
            view = await self._service.get_tool_view(
                actor=invocation.user,
                room_ids=invocation.user.room_ids,
                work_order_id=data.work_order_id,
            )
        except WorkOrderNotFound as exc:
            raise ToolForbidden() from exc
        if view.room_id is None:
            raise ToolDependencyUnavailable()
        item = view.work_order
        return WorkOrderGetOutput(
            work_order_id=item.id,
            status=item.status,
            room_id=view.room_id,
            fault_type=item.fault_category,
            description=item.description,
            created_at=item.created_at,
            events=tuple(
                WorkOrderEvent(
                    status=event.to_status,
                    occurred_at=event.created_at,
                    summary=STATUS_TEXT[event.to_status],
                )
                for event in view.events
            ),
        )
