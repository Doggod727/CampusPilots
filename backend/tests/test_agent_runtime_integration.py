import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.agent_platform.domain.contracts import (
    AgentResult, AgentTask, ToolCallRequest, UserContext,
)
from app.modules.agent_platform.orchestration.agent_registry import (
    AGENT_REGISTRATIONS, AgentRegistry,
)
from app.modules.agent_platform.orchestration.runtime import (
    AllowAgentSafety, BoundedGraphRuntime, DeterministicMockSpecialist,
    InMemoryRuntimeCheckpointStore, InMemoryRuntimeEventSink,
    SpecialistOutcome,
)
from app.modules.agent_platform.orchestration.router import RouterService
from app.modules.agent_platform.orchestration.supervisor import SupervisorPlanner
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.tool_gateway.executor import (
    AllowContentSafety, InMemoryApprovalVerifier, InMemoryAuditPort, ToolExecutor,
    canonical_arguments_hash,
)
from app.modules.agent_platform.tool_gateway.mocks import (
    build_mock_handlers, owned_lost_found_id,
)
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry
from app.modules.agent_platform.traces import AgentRunStateConflict, TraceService

USER_ID = UUID("10000000-0000-4000-8000-000000000001")
ROOM_ID = UUID("20000000-0000-4000-8000-000000000001")
RUN_ID = UUID("30000000-0000-4000-8000-000000000001")
STEP_ID = uuid4()
TOOL_CALL_ID = uuid4()
APPROVAL_ID = UUID("40000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 15, 14, tzinfo=UTC)


def _user(**overrides) -> UserContext:
    defaults = {
        "user_id": USER_ID,
        "username": "student01",
        "roles": ("student",),
        "permissions": (
            "agent:run",
            "knowledge:read",
            "electricity:read_own",
            "electricity:topup_request:create",
            "community:read",
            "community:write",
            "service:read",
            "work_order:create",
            "work_order:read",
            "moderation:execute",
            "audit:write",
            "model:read",
        ),
        "request_id": "request-0001",
        "room_ids": (ROOM_ID,),
    }
    return UserContext(**(defaults | overrides))


class _SpecialistWithToolCall:
    """A mock specialist that returns a deterministic tool call request."""

    def __init__(self, agent_code: str, tool_name: str, arguments: dict):
        self._agent_code = agent_code
        self._tool_name = tool_name
        self._arguments = arguments
        self.call_count = 0

    async def invoke(self, task: AgentTask, user: UserContext) -> SpecialistOutcome:
        self.call_count += 1
        return SpecialistOutcome(
            result=AgentResult(
                task_id=task.task_id,
                agent_code=self._agent_code,
                status="succeeded",
                summary=f"{self._agent_code} mock tool call",
                structured_output={},
            ),
            tool_request=ToolCallRequest(
                agent_run_id=task.agent_run_id,
                step_id=uuid4(),
                tool_name=self._tool_name,
                tool_version="1.0.0",
                arguments=self._arguments,
            ),
        )


def _make_tool_executor(
    verifier: InMemoryApprovalVerifier | None = None,
) -> tuple[ToolExecutor, InMemoryApprovalVerifier]:
    v = verifier if verifier is not None else InMemoryApprovalVerifier()
    executor = ToolExecutor(
        registry=ToolRegistry(TOOL_CONTRACTS.values()),
        handlers=build_mock_handlers(),
        content_safety=AllowContentSafety(),
        approval_verifier=v,
        audit=InMemoryAuditPort(),
    )
    return executor, v


def _build_runtime(
    *,
    specialists: dict | None = None,
    tool_executor: ToolExecutor | None = None,
    approval_service: MagicMock | None = None,
    user_agent_allowlists: dict | None = None,
) -> tuple[BoundedGraphRuntime, MagicMock, InMemoryRuntimeEventSink]:
    trace = MagicMock(spec=TraceService)
    trace.create_run.return_value = type("AgentRun", (), {
        "id": RUN_ID, "user_id": USER_ID, "client_request_id": "agent-request",
        "input_summary": "safe", "status": "created", "step_count": 0,
        "specialist_count": 0, "created_at": NOW, "updated_at": NOW,
    })()
    step = type("AgentStep", (), {
        "id": STEP_ID, "run_id": RUN_ID, "parent_step_id": None,
        "sequence_no": 1, "agent_code": "service_agent", "task_type": "generate",
        "status": "created", "input_summary": {}, "output_summary": {},
        "signature_hash": None, "created_at": NOW,
    })()
    tool_call = type("ToolCall", (), {
        "id": TOOL_CALL_ID, "run_id": RUN_ID, "step_id": STEP_ID,
        "tool_name": "mock", "tool_version": "1.0.0",
        "arguments_hash": "a" * 64, "arguments_summary": {},
        "result_summary": {}, "status": "prepared",
        "idempotency_key": None, "created_at": NOW,
    })()
    trace.transition_run = AsyncMock()
    trace.append_step = AsyncMock(return_value=step)
    trace.transition_step = AsyncMock()
    trace.append_tool = MagicMock(return_value=tool_call)
    trace.transition_tool = AsyncMock()
    trace.finalize = AsyncMock()

    events = InMemoryRuntimeEventSink()
    checkpoints = InMemoryRuntimeCheckpointStore()
    registry = AgentRegistry(AGENT_REGISTRATIONS)
    planner = SupervisorPlanner(registry=registry, max_steps=3, max_specialists=2)

    default_allowlists = {
        reg.definition.code: reg.version.tool_allowlist
        for reg in AGENT_REGISTRATIONS
    }
    if user_agent_allowlists:
        default_allowlists.update(user_agent_allowlists)

    if tool_executor is None:
        tool_executor, _ = _make_tool_executor()

    approvals = approval_service or MagicMock()

    runtime = BoundedGraphRuntime(
        router=RouterService(confidence_threshold=0.80),
        planner=planner,
        specialists=specialists or {},
        trace=trace,
        events=events,
        tool_executor=tool_executor,
        approval_service=approvals,
        agent_allowlists=default_allowlists,
        safety=AllowAgentSafety(),
        checkpoints=checkpoints,
    )
    return runtime, trace, events


def _compute_hash(tool_name: str, arguments: dict) -> str:
    contract = TOOL_CONTRACTS[tool_name]
    payload = contract.input_model.model_validate(arguments)
    return canonical_arguments_hash(payload)


# ---------------------------------------------------------------------------
# 1. 电费查询：用户询问电费余额，Agent 正确调用工具并返回结果
# ---------------------------------------------------------------------------

def test_electricity_balance_query_routes_and_executes_tool() -> None:
    """用户输入"查询电费" → 路由到 service_agent → 调用 electricity.get_balance → 返回余额"""
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.get_balance",
        {"room_id": ROOM_ID},
    )
    runtime, trace, events = _build_runtime(
        specialists={"service_agent": specialist},
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "查询我的电费余额有多少", {}))

    evt_list = events.list(RUN_ID)
    assert len(evt_list) >= 3
    assert evt_list[0].event == "route"
    assert evt_list[0].data["target_agent"] == "service"

    specialist_evts = [e for e in evt_list if e.event == "agent_step"]
    assert len(specialist_evts) == 1
    assert specialist_evts[0].data["status"] == "succeeded"
    assert specialist_evts[0].data["agent_code"] == "service_agent"

    done_events = [e for e in evt_list if e.event == "done"]
    assert len(done_events) == 1
    assert done_events[0].data["status"] == "succeeded"

    assert specialist.call_count == 1
    trace.finalize.assert_awaited_once()


def test_electricity_balance_query_explicit_routing_still_calls_tool() -> None:
    """显式指定 service_agent 模式时，跳过路由直接调用对应 specialist 及其工具"""
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.get_balance",
        {"room_id": ROOM_ID},
    )
    runtime, _, events = _build_runtime(
        specialists={"service_agent": specialist},
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "查询电费", {
        "requested_agent_codes": ("service_agent",),
    }))

    evt_list = events.list(RUN_ID)
    route_evt = [e for e in evt_list if e.event == "route"]
    assert len(route_evt) == 1
    assert route_evt[0].data["source"] == "rule"
    assert route_evt[0].data["reason_code"] == "ROUTE_EXPLICIT_AGENT_SELECTION"
    assert specialist.call_count == 1


# ---------------------------------------------------------------------------
# 2. 电费充值：含审批的完整 R2 工具流程
# ---------------------------------------------------------------------------

def test_electricity_topup_requires_approval_then_resumes_and_completes() -> None:
    """电费充值（R2 工具） → 触发审批 → 暂停运行 → 批准后恢复 → 执行成功"""
    arguments = {"room_id": ROOM_ID, "amount_cny": Decimal("20.00")}
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.create_topup_request", arguments,
    )
    approvals = MagicMock()
    approval_obj = MagicMock(id=APPROVAL_ID)
    approvals.create = AsyncMock(return_value=approval_obj)

    executor, verifier = _make_tool_executor()
    runtime, _, events = _build_runtime(
        specialists={"service_agent": specialist},
        tool_executor=executor,
        approval_service=approvals,
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "给我的房间充值20元电费", {}))

    evt_list = events.list(RUN_ID)
    approval_events = [e for e in evt_list if e.event == "approval_required"]
    assert len(approval_events) == 1
    assert approval_events[0].data["tool_name"] == "electricity.create_topup_request"
    approvals.create.assert_awaited_once()

    verifier.grant(
        approval_id=APPROVAL_ID, user_id=USER_ID,
        tool_name="electricity.create_topup_request", tool_version="1.0.0",
        arguments_hash=_compute_hash("electricity.create_topup_request", arguments),
    )

    asyncio.run(runtime.resume(RUN_ID, APPROVAL_ID))

    all_events = events.list(RUN_ID)
    done = [e for e in all_events if e.event == "done"]
    assert len(done) >= 1
    assert done[-1].data["status"] == "succeeded"


def test_topup_without_approval_pauses_and_does_not_execute() -> None:
    """缺少审批时，电费充值工具暂停（不执行 handler），等待审批"""
    arguments = {"room_id": ROOM_ID, "amount_cny": Decimal("50.00")}
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.create_topup_request", arguments,
    )
    approvals = MagicMock()
    approvals.create = AsyncMock(return_value=MagicMock(id=APPROVAL_ID))

    runtime, _, events = _build_runtime(
        specialists={"service_agent": specialist},
        approval_service=approvals,
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "充值50元电费", {}))

    approval_events = [e for e in events.list(RUN_ID) if e.event == "approval_required"]
    assert len(approval_events) == 1

    done = [e for e in events.list(RUN_ID) if e.event == "done"]
    assert len(done) == 0


def test_topup_mock_handler_returns_simulated_notice() -> None:
    """电费充值 mock handler 返回"模拟申请"提示，结果符合契约"""
    arguments = {"room_id": ROOM_ID, "amount_cny": Decimal("30.00")}
    executor, verifier = _make_tool_executor()

    verifier.grant(
        approval_id=APPROVAL_ID, user_id=USER_ID,
        tool_name="electricity.create_topup_request", tool_version="1.0.0",
        arguments_hash=_compute_hash("electricity.create_topup_request", arguments),
    )

    result = asyncio.run(executor.execute(
        context=_user(),
        request=ToolCallRequest(
            agent_run_id=RUN_ID, step_id=STEP_ID,
            tool_name="electricity.create_topup_request", tool_version="1.0.0",
            arguments=arguments,
            idempotency_key="idem-topup", approval_id=APPROVAL_ID,
        ),
        agent_allowlist=("electricity.create_topup_request",),
    ))

    assert result.status == "succeeded"
    assert result.data["amount"] == "30.00"
    assert result.data["notice"] == "模拟申请，不产生真实扣款或到账"
    assert result.data["status"] == "simulated"


# ---------------------------------------------------------------------------
# 3. 失物招领发布：含审批的完整 R2 工具流程
# ---------------------------------------------------------------------------

def test_lost_found_publish_requires_approval_and_then_publishes() -> None:
    """用户发布失物 → 路由 community_agent → lost_found.publish → 审批 → 恢复 → 发布成功"""
    arguments = {
        "item_type": "lost", "title": "校园卡", "category": "card",
        "location": "图书馆",
        "occurred_at": datetime(2026, 7, 15, 10, tzinfo=UTC),
        "description": "黑色卡套内有学生卡，急寻",
    }
    specialist = _SpecialistWithToolCall(
        "community_agent", "lost_found.publish", arguments,
    )
    approvals = MagicMock()
    approvals.create = AsyncMock(return_value=MagicMock(id=APPROVAL_ID))

    executor, verifier = _make_tool_executor()
    runtime, _, events = _build_runtime(
        specialists={"community_agent": specialist},
        tool_executor=executor,
        approval_service=approvals,
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "我丢了一张校园卡，帮我发布失物招领信息", {}))

    evt_list = events.list(RUN_ID)
    route = [e for e in evt_list if e.event == "route"]
    assert route[0].data["target_agent"] == "community"

    approval_evt = [e for e in evt_list if e.event == "approval_required"]
    assert len(approval_evt) == 1
    assert approval_evt[0].data["tool_name"] == "lost_found.publish"
    approvals.create.assert_awaited_once()

    verifier.grant(
        approval_id=APPROVAL_ID, user_id=USER_ID,
        tool_name="lost_found.publish", tool_version="1.0.0",
        arguments_hash=_compute_hash("lost_found.publish", arguments),
    )

    asyncio.run(runtime.resume(RUN_ID, APPROVAL_ID))

    all_events = events.list(RUN_ID)
    done = [e for e in all_events if e.event == "done"]
    assert done[-1].data["status"] == "succeeded"


def test_lost_found_publish_mock_handler_output_satisfies_contract() -> None:
    """失物招领 mock handler 返回 item_id 与 published 状态，符合契约"""
    arguments = {
        "item_type": "lost", "title": "钥匙", "category": "other",
        "location": "食堂",
        "occurred_at": datetime(2026, 7, 15, 12, tzinfo=UTC),
        "description": "一串宿舍钥匙，蓝色钥匙扣",
    }
    executor, verifier = _make_tool_executor()
    verifier.grant(
        approval_id=APPROVAL_ID, user_id=USER_ID,
        tool_name="lost_found.publish", tool_version="1.0.0",
        arguments_hash=_compute_hash("lost_found.publish", arguments),
    )
    result = asyncio.run(executor.execute(
        context=_user(),
        request=ToolCallRequest(
            agent_run_id=RUN_ID, step_id=STEP_ID,
            tool_name="lost_found.publish", tool_version="1.0.0",
            arguments=arguments,
            idempotency_key="idem-lf-pub", approval_id=APPROVAL_ID,
        ),
        agent_allowlist=("lost_found.publish",),
    ))
    assert result.status == "succeeded"
    assert result.data["status"] == "published"
    assert result.data["item_id"] is not None


def test_lost_found_publish_with_keywords_routes_to_community() -> None:
    """"失物"、"拾物"、"丢失"、"招领" 等关键词均路由到 community"""
    router = RouterService(confidence_threshold=0.80)
    for query in ("发布一个失物招领", "我丢失了校园卡", "拾物认领"):
        decision = asyncio.run(router.route(query))
        assert decision.target_agent == "community", f"'{query}' should route to community"
        assert decision.source == "rule"


# ---------------------------------------------------------------------------
# 4. 失物招领匹配查询：R1 工具无需审批
# ---------------------------------------------------------------------------

def test_lost_found_matches_query_completes_without_approval() -> None:
    """失物匹配查询（R1 工具）无需审批，直接返回匹配结果"""
    user = _user()
    item_id = owned_lost_found_id(user)
    specialist = _SpecialistWithToolCall(
        "community_agent", "lost_found.search_matches",
        {"item_id": item_id},
    )
    runtime, _, events = _build_runtime(
        specialists={"community_agent": specialist},
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "帮我搜索匹配的失物招领信息", {}))

    evt_list = events.list(RUN_ID)
    done = [e for e in evt_list if e.event == "done"]
    assert done[-1].data["status"] == "succeeded"
    assert specialist.call_count == 1

    approval_events = [e for e in evt_list if e.event == "approval_required"]
    assert len(approval_events) == 0


def test_lost_found_matches_mock_handler_returns_candidates() -> None:
    """失物匹配 mock handler 返回匹配候选（含 score、reasons、status）"""
    executor, _ = _make_tool_executor()
    user = _user()
    item_id = owned_lost_found_id(user)
    result = asyncio.run(executor.execute(
        context=user,
        request=ToolCallRequest(
            agent_run_id=RUN_ID, step_id=STEP_ID,
            tool_name="lost_found.search_matches", tool_version="1.0.0",
            arguments={"item_id": item_id},
        ),
        agent_allowlist=("lost_found.search_matches",),
    ))
    assert result.status == "succeeded"
    assert len(result.data["matches"]) >= 1
    match = result.data["matches"][0]
    assert match["score"] >= 0
    assert len(match["reasons"]) >= 1
    assert match["status"] == "candidate"


# ---------------------------------------------------------------------------
# 5. 综合编排与边界测试
# ---------------------------------------------------------------------------

def test_work_order_create_routes_to_service_and_triggers_approval() -> None:
    """创建报修工单 → 路由 service_agent → work_order.create → 审批暂停"""
    arguments = {
        "room_id": ROOM_ID, "fault_type": "electricity",
        "description": "宿舍电灯不亮需要维修服务",
    }
    specialist = _SpecialistWithToolCall(
        "service_agent", "work_order.create", arguments,
    )
    approvals = MagicMock()
    approvals.create = AsyncMock(return_value=MagicMock(id=APPROVAL_ID))

    runtime, _, events = _build_runtime(
        specialists={"service_agent": specialist},
        approval_service=approvals,
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "宿舍电灯不亮，帮我报修", {}))

    evt_list = events.list(RUN_ID)
    route = [e for e in evt_list if e.event == "route"]
    assert route[0].data["target_agent"] == "service"

    approval_evt = [e for e in evt_list if e.event == "approval_required"]
    assert len(approval_evt) == 1
    assert approval_evt[0].data["tool_name"] == "work_order.create"


def test_clarify_routing_when_no_keywords_match() -> None:
    """无关键词匹配时，路由返回 clarify，运行以 partial 终止"""
    runtime, _, events = _build_runtime()
    asyncio.run(runtime.start(RUN_ID, _user(), "今天天气怎么样", {}))

    evt_list = events.list(RUN_ID)
    done = [e for e in evt_list if e.event == "done"]
    assert len(done) == 1
    assert done[0].data["status"] == "partial"
    assert done[0].data["reason"] == "clarification_required"


def test_r2_tool_run_can_be_cancelled_while_awaiting_approval() -> None:
    """R2 工具等待审批时取消 Run → 运行终止"""
    arguments = {
        "room_id": ROOM_ID, "fault_type": "electricity",
        "description": "宿舍电灯不亮需要维修服务",
    }
    specialist = _SpecialistWithToolCall(
        "service_agent", "work_order.create", arguments,
    )
    approvals = MagicMock()
    approvals.create = AsyncMock(return_value=MagicMock(id=APPROVAL_ID))

    runtime, _, events = _build_runtime(
        specialists={"service_agent": specialist},
        approval_service=approvals,
    )
    asyncio.run(runtime.start(RUN_ID, _user(), "灯坏了快报修", {}))

    asyncio.run(runtime.cancel(RUN_ID))

    all_events = events.list(RUN_ID)
    done = [e for e in all_events if e.event == "done"]
    assert done[-1].data["status"] == "cancelled"


def test_event_register_triggers_approval_for_r2_tool() -> None:
    """活动报名（R2 工具） → 审批暂停"""
    event_id = uuid4()
    specialist = _SpecialistWithToolCall(
        "community_agent", "event.register",
        {"event_id": event_id},
    )
    approvals = MagicMock()
    approvals.create = AsyncMock(return_value=MagicMock(id=APPROVAL_ID))

    runtime, _, events = _build_runtime(
        specialists={"community_agent": specialist},
        approval_service=approvals,
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "帮我报名校园志愿活动", {}))

    evt_list = events.list(RUN_ID)
    route = [e for e in evt_list if e.event == "route"]
    assert route[0].data["target_agent"] == "community"

    approval_evt = [e for e in evt_list if e.event == "approval_required"]
    assert len(approval_evt) == 1
    assert approval_evt[0].data["tool_name"] == "event.register"


def test_knowledge_search_completes_without_approval() -> None:
    """知识搜索（R0 工具）→ 直接完成，不需要审批"""
    specialist = _SpecialistWithToolCall(
        "knowledge_agent", "knowledge.search",
        {"query": "图书馆开放时间"},
    )
    runtime, _, events = _build_runtime(
        specialists={"knowledge_agent": specialist},
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "图书馆几点开门", {}))

    evt_list = events.list(RUN_ID)
    done = [e for e in evt_list if e.event == "done"]
    assert done[-1].data["status"] == "succeeded"

    approval_evt = [e for e in evt_list if e.event == "approval_required"]
    assert len(approval_evt) == 0


def test_resume_on_nonexistent_run_raises_conflict() -> None:
    """对不存在的 Run 执行 resume → 抛出状态冲突"""
    runtime, _, _ = _build_runtime()
    with pytest.raises(AgentRunStateConflict):
        asyncio.run(runtime.resume(RUN_ID, APPROVAL_ID))


def test_resume_on_finished_run_raises_conflict() -> None:
    """对已完成的 Run 执行 resume → 抛出状态冲突"""
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.get_balance",
        {"room_id": ROOM_ID},
    )
    runtime, _, _ = _build_runtime(
        specialists={"service_agent": specialist},
    )
    asyncio.run(runtime.start(RUN_ID, _user(), "电费查询", {}))

    with pytest.raises(AgentRunStateConflict):
        asyncio.run(runtime.resume(RUN_ID, APPROVAL_ID))


def test_cancel_on_finished_run_is_noop() -> None:
    """对已完成的 Run 执行 cancel → 无操作（幂等）"""
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.get_balance",
        {"room_id": ROOM_ID},
    )
    runtime, _, _ = _build_runtime(
        specialists={"service_agent": specialist},
    )
    asyncio.run(runtime.start(RUN_ID, _user(), "电费余额查询", {}))

    result = asyncio.run(runtime.cancel(RUN_ID))
    assert result is None


def test_service_guide_tool_executes_through_executor() -> None:
    """办事指南工具 → 查询并返回指南项"""
    executor, _ = _make_tool_executor()
    result = asyncio.run(executor.execute(
        context=_user(),
        request=ToolCallRequest(
            agent_run_id=RUN_ID, step_id=STEP_ID,
            tool_name="service.get_guide", tool_version="1.0.0",
            arguments={"query": "补办学生证"},
        ),
        agent_allowlist=("service.get_guide",),
    ))
    assert result.status == "succeeded"
    assert len(result.data["items"]) >= 1
    guide = result.data["items"][0]
    assert "补办学生证" in guide["title"]


def test_agent_orchestration_publishes_events_in_order() -> None:
    """验证事件发布时间顺序：route → agent_step → done"""
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.get_balance",
        {"room_id": ROOM_ID},
    )
    runtime, _, events = _build_runtime(
        specialists={"service_agent": specialist},
    )

    asyncio.run(runtime.start(RUN_ID, _user(), "电费剩余多少钱", {}))

    evt_list = events.list(RUN_ID)
    event_types = tuple(e.event for e in evt_list)
    assert "route" in event_types
    assert "agent_step" in event_types
    assert "done" in event_types
    assert event_types.index("route") < event_types.index("agent_step")
    assert event_types.index("agent_step") < event_types.index("done")


def test_injected_tool_names_intersected_with_agent_allowlist() -> None:
    """requested_tool_names 会与 agent_allowlist 取交集，
       当用户请求的工具不在 agent 允许列表中时，allowlist 为空导致工具被拒"""
    specialist = _SpecialistWithToolCall(
        "service_agent", "electricity.get_balance",
        {"room_id": ROOM_ID},
    )
    runtime, _, events = _build_runtime(
        specialists={"service_agent": specialist},
        user_agent_allowlists={
            "service_agent": ("electricity.get_balance",),
        },
    )

    with pytest.raises(Exception):
        asyncio.run(runtime.start(RUN_ID, _user(), "查电费", {
            "requested_tool_names": ("service.get_guide",),
        }))


def test_duplicate_start_on_same_run_raises_conflict() -> None:
    """对同一个 Run 重复 start → 抛出状态冲突"""
    runtime, _, _ = _build_runtime()
    asyncio.run(runtime.start(RUN_ID, _user(), "查电费", {}))
    with pytest.raises(AgentRunStateConflict):
        asyncio.run(runtime.start(RUN_ID, _user(), "再查一次", {}))


def test_electricity_keyword_routes_to_service() -> None:
    """关键字"电费"路由到 service"""
    router = RouterService(confidence_threshold=0.80)
    for query in ("查询电费", "电费余额", "充值电费", "宿舍电费查询"):
        decision = asyncio.run(router.route(query))
        assert decision.target_agent == "service", f"'{query}' → service, got {decision.target_agent}"


def test_lost_found_keyword_routes_to_community() -> None:
    """"失物"、"招领"、"丢失"、"拾物" 均路由到 community"""
    router = RouterService(confidence_threshold=0.80)
    for query in ("发布失物招领", "丢失校园卡", "捡到拾物", "失物寻找"):
        decision = asyncio.run(router.route(query))
        assert decision.target_agent == "community", f"'{query}' → community, got {decision.target_agent}"
