import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.modules.agent_platform.domain.contracts import ToolInvocationContext, UserContext
from app.modules.agent_platform.tool_gateway.catalog import (
    Citation,
    ElectricityBalanceInput,
    ElectricityBalanceOutput,
    ElectricityTopupInput,
    ElectricityTopupOutput,
    EventItem,
    EventRegisterInput,
    EventRegisterOutput,
    EventSearchInput,
    EventSearchOutput,
    GovernanceAuditInput,
    GovernanceAuditOutput,
    GovernanceAuthorizeInput,
    GovernanceAuthorizeOutput,
    GovernanceCheckInput,
    GovernanceCheckOutput,
    GuideItem,
    KnowledgeAnswerInput,
    KnowledgeAnswerOutput,
    KnowledgeSearchInput,
    KnowledgeSearchItem,
    KnowledgeSearchOutput,
    LostFoundMatch,
    LostFoundMatchesInput,
    LostFoundMatchesOutput,
    LostFoundPublishInput,
    LostFoundPublishOutput,
    ServiceGuideInput,
    ServiceGuideOutput,
    ToolModel,
    WorkOrderCreateInput,
    WorkOrderCreateOutput,
    WorkOrderEvent,
    WorkOrderGetInput,
    WorkOrderGetOutput,
)

FIXED_NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def deterministic_id(kind: str, value: object) -> UUID:
    return uuid5(NAMESPACE_URL, f"campuspilot:{kind}:{value}")


def owned_work_order_id(context: UserContext) -> UUID:
    return deterministic_id("work-order", context.user_id)


def owned_lost_found_id(context: UserContext) -> UUID:
    return deterministic_id("lost-found", context.user_id)


class MockScenario(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    CONFLICT = "conflict"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    TIMEOUT = "timeout"


class MockToolConflict(RuntimeError):
    pass


class MockDependencyUnavailable(RuntimeError):
    pass


class MockResourceForbidden(PermissionError):
    pass


class ToolHandler(Protocol):
    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> ToolModel: ...


HandlerFunction = Callable[[UserContext, ToolModel, bool], Awaitable[ToolModel]]


class MockToolHandler:
    def __init__(
        self,
        function: HandlerFunction,
        scenario: MockScenario = MockScenario.SUCCESS,
        timeout_seconds: float = 0.05,
    ) -> None:
        self._function = function
        self._scenario = scenario
        self._timeout_seconds = timeout_seconds
        self.call_count = 0

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> ToolModel:
        self.call_count += 1
        if self._scenario == MockScenario.CONFLICT:
            raise MockToolConflict("mock domain conflict")
        if self._scenario == MockScenario.DEPENDENCY_UNAVAILABLE:
            raise MockDependencyUnavailable("mock dependency unavailable")
        if self._scenario == MockScenario.TIMEOUT:
            await asyncio.sleep(self._timeout_seconds)
        return await self._function(
            invocation.user, payload, self._scenario == MockScenario.EMPTY
        )


async def _knowledge_search(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = KnowledgeSearchInput.model_validate(payload)
    items = () if empty else (
        KnowledgeSearchItem(
            chunk_id=deterministic_id("chunk", data.query),
            document_id=deterministic_id("document", data.query),
            title="校园服务演示知识",
            snippet=f"与“{data.query}”相关的脱敏演示片段",
            score=0.91,
            source_location="mock://knowledge",
            page_number=1,
        ),
    )
    return KnowledgeSearchOutput(items=items[: data.top_k], retrieval_version="mock-v1")


async def _knowledge_answer(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = KnowledgeAnswerInput.model_validate(payload)
    citations = () if empty else (
        Citation(
            chunk_id=deterministic_id("chunk", data.question),
            document_id=deterministic_id("document", data.question),
            title="校园服务演示知识",
            quote="这是用于离线测试的脱敏引用。",
        ),
    )
    return KnowledgeAnswerOutput(
        answer="未找到足够依据。" if empty else f"关于“{data.question}”的演示回答。",
        citations=citations,
        message_id=deterministic_id("message", data.question),
        usage={"prompt_tokens": 10, "completion_tokens": 8},
        finish_reason="stop",
    )


async def _service_guide(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = ServiceGuideInput.model_validate(payload)
    items = () if empty else (
        GuideItem(
            guide_id=deterministic_id("guide", data.query),
            title=f"{data.query}办理指南",
            summary="离线演示指南",
            location="学生服务中心",
            updated_at=FIXED_NOW,
            steps=("准备材料", "前往窗口"),
        ),
    )
    return ServiceGuideOutput(items=items)


def _require_room(context: UserContext, room_id: UUID) -> None:
    if room_id not in context.room_ids:
        raise MockResourceForbidden("room is outside the caller scope")


async def _work_order_create(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = WorkOrderCreateInput.model_validate(payload)
    if data.room_id is not None:
        _require_room(context, data.room_id)
    return WorkOrderCreateOutput(
        work_order_id=owned_work_order_id(context),
        status="submitted",
        created_at=FIXED_NOW,
    )


async def _work_order_get(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = WorkOrderGetInput.model_validate(payload)
    if data.work_order_id != owned_work_order_id(context) or not context.room_ids:
        raise MockResourceForbidden("work order is outside the caller scope")
    return WorkOrderGetOutput(
        work_order_id=data.work_order_id,
        status="submitted",
        room_id=context.room_ids[0],
        fault_type="water",
        description="宿舍水龙头持续漏水，需要检修。",
        created_at=FIXED_NOW,
        events=(WorkOrderEvent(status="submitted", occurred_at=FIXED_NOW),),
    )


async def _electricity_balance(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = ElectricityBalanceInput.model_validate(payload)
    if data.room_id is not None:
        _require_room(context, data.room_id)
    return ElectricityBalanceOutput(
        room_id=data.room_id or (context.room_ids[0] if context.room_ids else UUID(int=0)),
        balance=Decimal("42.50"),
        updated_at=FIXED_NOW,
    )


async def _electricity_topup(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = ElectricityTopupInput.model_validate(payload)
    if data.room_id is not None:
        _require_room(context, data.room_id)
    room_ref = data.room_id or (context.room_ids[0] if context.room_ids else UUID(int=0))
    return ElectricityTopupOutput(
        topup_request_id=deterministic_id("topup", f"{context.user_id}:{room_ref}:{data.amount_cny}"),
        amount=data.amount_cny,
    )


async def _event_search(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = EventSearchInput.model_validate(payload)
    items = () if empty else (
        EventItem(
            event_id=deterministic_id("event", data.query or "all"),
            title="校园志愿活动",
            starts_at=FIXED_NOW,
            remaining_capacity=8,
        ),
    )
    return EventSearchOutput(
        items=items, page=data.page, page_size=data.page_size, total=len(items)
    )


async def _event_register(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = EventRegisterInput.model_validate(payload)
    return EventRegisterOutput(
        registration_id=deterministic_id("registration", f"{context.user_id}:{data.event_id}")
    )


async def _lost_found_publish(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    LostFoundPublishInput.model_validate(payload)
    return LostFoundPublishOutput(item_id=owned_lost_found_id(context), status="published")


async def _lost_found_matches(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = LostFoundMatchesInput.model_validate(payload)
    if data.item_id != owned_lost_found_id(context):
        raise MockResourceForbidden("item is outside the caller scope")
    matches = () if empty else (
        LostFoundMatch(
            matched_item_id=deterministic_id("match", data.item_id),
            score=0.82,
            reasons=("类别相同", "地点接近"),
            status="candidate",
        ),
    )
    return LostFoundMatchesOutput(matches=matches[: data.limit])


async def _governance_check(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = GovernanceCheckInput.model_validate(payload)
    blocked = "blocked" in data.text.lower()
    return GovernanceCheckOutput(
        risk_level="high" if blocked else "low",
        action="block" if blocked else "allow",
        hits=(),
        sanitized_text="***" if blocked else data.text,
        policy_version="mock-policy-v1",
    )


async def _governance_authorize(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = GovernanceAuthorizeInput.model_validate(payload)
    allowed = data.user_id == context.user_id
    return GovernanceAuthorizeOutput(
        allowed=allowed, reason_code=None if allowed else "USER_CONTEXT_MISMATCH"
    )


async def _governance_audit(
    context: UserContext, payload: ToolModel, empty: bool
) -> ToolModel:
    data = GovernanceAuditInput.model_validate(payload)
    return GovernanceAuditOutput(
        audit_id=deterministic_id("audit", f"{context.user_id}:{data.request_id}:{data.action}")
    )


MOCK_FUNCTIONS: dict[str, HandlerFunction] = {
    "knowledge.search": _knowledge_search,
    "knowledge.answer": _knowledge_answer,
    "service.get_guide": _service_guide,
    "work_order.create": _work_order_create,
    "work_order.get": _work_order_get,
    "electricity.get_balance": _electricity_balance,
    "electricity.create_topup_request": _electricity_topup,
    "event.search": _event_search,
    "event.register": _event_register,
    "lost_found.publish": _lost_found_publish,
    "lost_found.search_matches": _lost_found_matches,
    "governance.check_content": _governance_check,
    "governance.authorize_tool": _governance_authorize,
    "governance.write_audit": _governance_audit,
}


def build_mock_handlers(
    scenarios: Mapping[str, MockScenario] | None = None,
    *,
    timeout_seconds: float = 0.05,
) -> dict[str, MockToolHandler]:
    selected = scenarios or {}
    return {
        name: MockToolHandler(
            function,
            scenario=selected.get(name, MockScenario.SUCCESS),
            timeout_seconds=timeout_seconds,
        )
        for name, function in MOCK_FUNCTIONS.items()
    }
