import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import ToolCallRequest, UserContext
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS, ToolModel
from app.modules.agent_platform.tool_gateway.executor import (
    AllowContentSafety,
    InMemoryApprovalVerifier,
    InMemoryAuditPort,
    ToolExecutor,
    canonical_arguments_hash,
)
from app.modules.agent_platform.tool_gateway.mocks import (
    MockScenario,
    build_mock_handlers,
    owned_lost_found_id,
    owned_work_order_id,
)
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry


ROOM_ID = UUID("20000000-0000-4000-8000-000000000001")


def _context(*, all_permissions: bool = True) -> UserContext:
    permissions = sorted({
        permission
        for contract in TOOL_CONTRACTS.values()
        for permission in contract.definition.required_permissions
    }) if all_permissions else []
    return UserContext(
        user_id=UUID("10000000-0000-4000-8000-000000000001"),
        username="student01",
        roles=("student",),
        permissions=permissions,
        request_id="request-123",
        campus_id="main",
        room_ids=(ROOM_ID,),
    )


def _samples(context: UserContext) -> dict[str, dict[str, object]]:
    return {
        "knowledge.search": {"query": "图书馆开放时间"},
        "knowledge.answer": {"question": "图书馆几点关闭？"},
        "service.get_guide": {"query": "补办学生证"},
        "work_order.create": {
            "room_id": ROOM_ID, "fault_type": "water",
            "description": "宿舍水龙头持续漏水，需要安排检修。",
        },
        "work_order.get": {"work_order_id": owned_work_order_id(context)},
        "electricity.get_balance": {"room_id": ROOM_ID},
        "electricity.create_topup_request": {"room_id": ROOM_ID, "amount": Decimal("20.00")},
        "event.search": {"query": "志愿"},
        "event.register": {"event_id": uuid4()},
        "lost_found.publish": {
            "item_type": "lost", "title": "校园卡", "category": "card",
            "location": "图书馆", "occurred_at": datetime(2026, 7, 15, tzinfo=UTC),
            "description": "丢失一张校园卡",
        },
        "lost_found.search_matches": {"item_id": owned_lost_found_id(context)},
        "governance.check_content": {"text": "safe content", "scope": "tool_input"},
        "governance.authorize_tool": {
            "user_id": context.user_id, "agent_code": "service_agent",
            "tool_name": "service.get_guide",
        },
        "governance.write_audit": {
            "action": "tool.execute", "request_id": context.request_id,
            "result": "success",
        },
    }


def _executor(*, handlers=None, registry=None):
    verifier = InMemoryApprovalVerifier()
    audit = InMemoryAuditPort()
    executor = ToolExecutor(
        registry=registry or ToolRegistry(TOOL_CONTRACTS.values()),
        handlers=handlers or build_mock_handlers(),
        content_safety=AllowContentSafety(),
        approval_verifier=verifier,
        audit=audit,
    )
    return executor, verifier, audit


class RecordingSafety(AllowContentSafety):
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.outputs: list[str] = []

    async def check_input(self, context, definition, payload) -> None:
        self.inputs.append(definition.name)

    async def check_output(self, context, definition, payload) -> None:
        self.outputs.append(definition.name)


def _executor_with_safety(safety: RecordingSafety):
    verifier = InMemoryApprovalVerifier()
    audit = InMemoryAuditPort()
    executor = ToolExecutor(
        registry=ToolRegistry(TOOL_CONTRACTS.values()),
        handlers=build_mock_handlers(),
        content_safety=safety,
        approval_verifier=verifier,
        audit=audit,
    )
    return executor, verifier, audit


def _request(
    name: str,
    arguments: dict[str, object],
    *,
    approval_id=None,
    idempotency_key=None,
) -> ToolCallRequest:
    return ToolCallRequest(
        agent_run_id=UUID("30000000-0000-4000-8000-000000000001"),
        step_id=uuid4(),
        tool_name=name,
        tool_version="1.0.0",
        arguments=arguments,
        idempotency_key=idempotency_key,
        approval_id=approval_id,
    )


def test_all_fourteen_mock_tools_execute_through_one_pipeline() -> None:
    context = _context()
    samples = _samples(context)
    safety = RecordingSafety()
    executor, verifier, audit = _executor_with_safety(safety)
    requests = []
    for name, contract in TOOL_CONTRACTS.items():
        approval_id = uuid4() if contract.definition.requires_approval else None
        idempotency_key = f"idem-{name}" if contract.definition.risk_level in {"r2", "r3"} and contract.definition.visibility != "runtime_internal" else None
        request = _request(
            name, samples[name], approval_id=approval_id,
            idempotency_key=idempotency_key,
        )
        if approval_id is not None:
            payload = contract.input_model.model_validate(samples[name])
            verifier.grant(
                approval_id=approval_id,
                user_id=context.user_id,
                tool_name=name,
                tool_version="1.0.0",
                arguments_hash=canonical_arguments_hash(payload),
            )
        requests.append(request)

    async def run_all():
        return [
            await executor.execute(
                context=context,
                request=request,
                agent_allowlist=TOOL_CONTRACTS,
                trusted_runtime=True,
            )
            for request in requests
        ]

    results = asyncio.run(run_all())
    assert len(results) == 14
    assert all(result.status == "succeeded" for result in results)
    assert all(result.audit_id is not None for result in results)
    assert len(audit.events) == 14
    assert all(event.result == "success" for event in audit.events)
    assert sorted(safety.inputs) == sorted(TOOL_CONTRACTS)
    assert sorted(safety.outputs) == sorted(TOOL_CONTRACTS)


@pytest.mark.parametrize(
    ("arguments", "idempotency_key", "approval_id", "expected_code"),
    [
        ({"room_id": str(ROOM_ID), "fault_type": "water", "description": "short"}, "idem", None, "TOOL_ARGUMENT_INVALID"),
        ({"room_id": str(ROOM_ID), "fault_type": "water", "description": "宿舍水龙头持续漏水，需要检修。"}, None, None, "TOOL_ARGUMENT_INVALID"),
        ({"room_id": str(ROOM_ID), "fault_type": "water", "description": "宿舍水龙头持续漏水，需要检修。"}, "idem", None, "TOOL_APPROVAL_REQUIRED"),
        ({"room_id": str(ROOM_ID), "fault_type": "water", "description": "宿舍水龙头持续漏水，需要检修。"}, "idem", UUID("40000000-0000-4000-8000-000000000001"), "TOOL_APPROVAL_INVALID"),
    ],
)
def test_invalid_write_calls_never_reach_handler(
    arguments, idempotency_key, approval_id, expected_code
) -> None:
    context = _context()
    handlers = build_mock_handlers()
    executor, _, audit = _executor(handlers=handlers)
    request = _request(
        "work_order.create", arguments,
        idempotency_key=idempotency_key, approval_id=approval_id,
    )
    with pytest.raises(AppError) as error:
        asyncio.run(executor.execute(
            context=context, request=request,
            agent_allowlist=("work_order.create",),
        ))
    assert error.value.code == expected_code
    assert handlers["work_order.create"].call_count == 0
    assert audit.events[-1].error_code == expected_code
    assert "宿舍" not in str(error.value)


def test_permission_allowlist_and_runtime_visibility_default_to_deny() -> None:
    handlers = build_mock_handlers()
    executor, _, audit = _executor(handlers=handlers)
    context = _context(all_permissions=False)
    request = _request("knowledge.search", {"query": "secret input"})
    with pytest.raises(AppError) as forbidden:
        asyncio.run(executor.execute(
            context=context, request=request,
            agent_allowlist=("knowledge.search",),
        ))
    assert forbidden.value.code == "TOOL_FORBIDDEN"
    assert "secret input" not in str(forbidden.value)
    assert handlers["knowledge.search"].call_count == 0
    assert audit.events[-1].result == "denied"

    internal_context = _context()
    internal = _request(
        "governance.check_content", {"text": "safe", "scope": "tool_input"}
    )
    with pytest.raises(AppError) as internal_forbidden:
        asyncio.run(executor.execute(
            context=internal_context, request=internal,
            agent_allowlist=("governance.check_content",),
            trusted_runtime=False,
        ))
    assert internal_forbidden.value.code == "TOOL_FORBIDDEN"
    assert handlers["governance.check_content"].call_count == 0


def test_timeout_and_dependency_failures_are_safely_mapped() -> None:
    context = _context()
    base = TOOL_CONTRACTS["knowledge.search"]
    fast = replace(
        base,
        definition=base.definition.model_copy(update={"timeout_ms": 100}),
    )
    registry = ToolRegistry([fast])
    timeout_handlers = build_mock_handlers(
        {"knowledge.search": MockScenario.TIMEOUT}, timeout_seconds=0.2
    )
    timeout_executor, _, timeout_audit = _executor(
        handlers=timeout_handlers, registry=registry
    )
    request = _request("knowledge.search", {"query": "safe"})
    with pytest.raises(AppError) as timeout:
        asyncio.run(timeout_executor.execute(
            context=context, request=request,
            agent_allowlist=("knowledge.search",),
        ))
    assert timeout.value.code == "TOOL_TIMEOUT"
    assert timeout.value.status_code == 504
    assert timeout_audit.events[-1].error_code == "TOOL_TIMEOUT"

    dependency_handlers = build_mock_handlers({
        "knowledge.search": MockScenario.DEPENDENCY_UNAVAILABLE
    })
    dependency_executor, _, dependency_audit = _executor(
        handlers=dependency_handlers
    )
    with pytest.raises(AppError) as dependency:
        asyncio.run(dependency_executor.execute(
            context=context, request=request,
            agent_allowlist=("knowledge.search",),
        ))
    assert dependency.value.code == "TOOL_DEPENDENCY_UNAVAILABLE"
    assert dependency.value.status_code == 502
    assert dependency_audit.events[-1].error_code == "TOOL_DEPENDENCY_UNAVAILABLE"


def test_canonical_hash_ignores_object_key_order() -> None:
    model = TOOL_CONTRACTS["event.search"].input_model
    left = model.model_validate({"query": "x", "page": 1, "page_size": 20})
    right = model.model_validate({"page_size": 20, "page": 1, "query": "x"})
    assert canonical_arguments_hash(left) == canonical_arguments_hash(right)


def test_approval_is_hash_bound_and_consumed_once() -> None:
    context = _context()
    handlers = build_mock_handlers()
    executor, verifier, audit = _executor(handlers=handlers)
    approval_id = uuid4()
    arguments = _samples(context)["event.register"]
    payload = TOOL_CONTRACTS["event.register"].input_model.model_validate(arguments)
    verifier.grant(
        approval_id=approval_id,
        user_id=context.user_id,
        tool_name="event.register",
        tool_version="1.0.0",
        arguments_hash=canonical_arguments_hash(payload),
    )
    request = _request(
        "event.register", arguments,
        idempotency_key="event-register", approval_id=approval_id,
    )

    asyncio.run(executor.execute(
        context=context, request=request,
        agent_allowlist=("event.register",),
    ))
    with pytest.raises(AppError) as replay:
        asyncio.run(executor.execute(
            context=context, request=request,
            agent_allowlist=("event.register",),
        ))

    assert replay.value.code == "TOOL_APPROVAL_INVALID"
    assert handlers["event.register"].call_count == 1
    assert audit.events[-1].error_code == "TOOL_APPROVAL_INVALID"


def test_invalid_handler_output_is_safely_rejected_and_audited() -> None:
    async def invalid_output(context, payload):
        return {"unexpected": "internal output"}

    context = _context()
    executor, _, audit = _executor(
        handlers={"knowledge.search": invalid_output}
    )
    request = _request("knowledge.search", {"query": "safe"})

    with pytest.raises(AppError) as error:
        asyncio.run(executor.execute(
            context=context,
            request=request,
            agent_allowlist=("knowledge.search",),
        ))

    assert error.value.code == "TOOL_DEPENDENCY_UNAVAILABLE"
    assert "internal output" not in str(error.value)
    assert audit.events[-1].error_code == "TOOL_DEPENDENCY_UNAVAILABLE"
