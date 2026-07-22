import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.modules.agent_platform.domain.contracts import ToolInvocationContext, UserContext
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.tool_gateway.mocks import (
    MockDependencyUnavailable,
    MockResourceForbidden,
    MockScenario,
    MockToolConflict,
    build_mock_handlers,
    owned_lost_found_id,
    owned_work_order_id,
)


def _context(room_id: UUID | None = None) -> UserContext:
    return UserContext(
        user_id=UUID("10000000-0000-4000-8000-000000000001"),
        username="student01",
        roles=("student",),
        permissions=("agent:run",),
        request_id="request-123",
        campus_id="main",
        room_ids=() if room_id is None else (room_id,),
    )


def _invocation(context: UserContext) -> ToolInvocationContext:
    return ToolInvocationContext(
        user=context,
        agent_run_id=UUID("30000000-0000-4000-8000-000000000001"),
        step_id=UUID("30000000-0000-4000-8000-000000000002"),
        arguments_hash="a" * 64,
    )


def _samples(context: UserContext, room_id: UUID) -> dict[str, dict[str, object]]:
    return {
        "knowledge.search": {"query": "图书馆开放时间"},
        "knowledge.answer": {"question": "图书馆几点关闭？"},
        "service.get_guide": {"query": "补办学生证", "campus_id": "main"},
        "work_order.create": {
            "room_id": room_id, "fault_type": "water",
            "description": "宿舍水龙头持续漏水，需要安排检修。",
        },
        "work_order.get": {"work_order_id": owned_work_order_id(context)},
        "electricity.get_balance": {"room_id": room_id},
        "electricity.create_topup_request": {"room_id": room_id, "amount_cny": Decimal("20.00")},
        "event.search": {"query": "志愿"},
        "event.register": {"event_id": uuid4()},
        "lost_found.publish": {
            "item_type": "lost", "title": "校园卡", "category": "card",
            "location": "图书馆", "occurred_at": datetime(2026, 7, 15, tzinfo=UTC),
            "description": "丢失一张校园卡", "contact_preference": "in_app",
        },
        "lost_found.search_matches": {"item_id": owned_lost_found_id(context)},
        "governance.check_content": {"text": "safe content", "scope": "tool_input"},
        "governance.authorize_tool": {
            "user_id": context.user_id, "agent_code": "service_agent",
            "tool_name": "service.get_guide", "resource": {},
        },
        "governance.write_audit": {
            "action": "tool.execute", "request_id": context.request_id,
            "result": "success", "metadata": {},
        },
    }


def test_all_baseline_mock_handlers_return_frozen_output_models() -> None:
    room_id = UUID("20000000-0000-4000-8000-000000000001")
    context = _context(room_id)
    handlers = build_mock_handlers()
    samples = _samples(context, room_id)

    async def run_all():
        results = {}
        for name in handlers:
            contract = TOOL_CONTRACTS[name]
            payload = contract.input_model.model_validate(samples[name])
            output = await handlers[name](_invocation(context), payload)
            results[name] = contract.output_model.model_validate(output)
        return results

    outputs = asyncio.run(run_all())
    assert set(outputs) == set(handlers)
    assert outputs["electricity.get_balance"].currency == "CNY"
    assert outputs["electricity.get_balance"].source == "mock"
    assert outputs["electricity.get_balance"].is_simulated is True
    assert outputs["electricity.create_topup_request"].notice == "模拟申请，不产生真实扣款或到账"

    rendered = " ".join(
        str(output.model_dump(mode="json")) for output in outputs.values()
    ).lower()
    assert "password" not in rendered
    assert "access_token" not in rendered
    assert "refresh_token" not in rendered
    assert "authorization" not in rendered
    assert "phone" not in rendered


def test_empty_scenario_is_explicit_and_schema_valid() -> None:
    context = _context()
    handlers = build_mock_handlers({
        "knowledge.search": MockScenario.EMPTY,
        "service.get_guide": MockScenario.EMPTY,
        "event.search": MockScenario.EMPTY,
        "lost_found.search_matches": MockScenario.EMPTY,
    })
    samples = _samples(context, uuid4())

    async def run_empty():
        outputs = {}
        for name in (
            "knowledge.search", "service.get_guide", "event.search",
            "lost_found.search_matches",
        ):
            contract = TOOL_CONTRACTS[name]
            payload = contract.input_model.model_validate(samples[name])
            outputs[name] = await handlers[name](_invocation(context), payload)
        return outputs

    outputs = asyncio.run(run_empty())
    assert outputs["knowledge.search"].items == ()
    assert outputs["service.get_guide"].items == ()
    assert outputs["event.search"].items == ()
    assert outputs["lost_found.search_matches"].matches == ()


def test_failure_scenarios_are_explicit_and_deterministic() -> None:
    context = _context()
    sample = TOOL_CONTRACTS["knowledge.search"].input_model.model_validate({"query": "test"})

    conflict = build_mock_handlers({"knowledge.search": MockScenario.CONFLICT})["knowledge.search"]
    dependency = build_mock_handlers({"knowledge.search": MockScenario.DEPENDENCY_UNAVAILABLE})["knowledge.search"]
    timeout = build_mock_handlers(
        {"knowledge.search": MockScenario.TIMEOUT}, timeout_seconds=0.05
    )["knowledge.search"]

    with pytest.raises(MockToolConflict):
        asyncio.run(conflict(_invocation(context), sample))
    with pytest.raises(MockDependencyUnavailable):
        asyncio.run(dependency(_invocation(context), sample))
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(timeout(_invocation(context), sample), timeout=0.001))


def test_room_and_owner_scopes_are_enforced() -> None:
    allowed_room = uuid4()
    context = _context(allowed_room)
    handlers = build_mock_handlers()

    wrong_room = TOOL_CONTRACTS["electricity.get_balance"].input_model.model_validate(
        {"room_id": uuid4()}
    )
    wrong_item = TOOL_CONTRACTS["lost_found.search_matches"].input_model.model_validate(
        {"item_id": uuid4()}
    )

    with pytest.raises(MockResourceForbidden):
        asyncio.run(handlers["electricity.get_balance"](_invocation(context), wrong_room))
    with pytest.raises(MockResourceForbidden):
        asyncio.run(handlers["lost_found.search_matches"](_invocation(context), wrong_item))
