from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent_platform.domain.contracts import (
    AgentTask,
    ApprovalRequest,
    RouteDecision,
    ToolCallRequest,
    ToolDefinition,
    UserContext,
)


def _definition(**overrides: object) -> ToolDefinition:
    values = {
        "name": "knowledge.search",
        "version": "1.0.0",
        "module": "m1",
        "description": "Search knowledge",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_permissions": ["knowledge:read"],
        "risk_level": "r0",
        "timeout_ms": 5000,
        "idempotent": True,
        "requires_approval": False,
        "visibility": "agent",
        "enabled": True,
    }
    values.update(overrides)
    return ToolDefinition.model_validate(values)


def test_context_and_task_collections_are_stable_unique_tuples() -> None:
    room_a, room_b = uuid4(), uuid4()
    context = UserContext(
        user_id=uuid4(), username="student01",
        roles=["student", "student"],
        permissions=["service:read", "agent:run", "service:read"],
        request_id="request-123", room_ids=[room_b, room_a, room_b],
    )
    dependency = uuid4()
    task = AgentTask(
        task_id=uuid4(), agent_run_id=uuid4(), target_agent="service_agent",
        objective="Find a guide", depends_on=[dependency, dependency],
        constraints=["safe", "safe"], max_steps=6,
    )

    assert context.roles == ("student",)
    assert context.permissions == ("agent:run", "service:read")
    assert context.room_ids == tuple(sorted((room_a, room_b), key=str))
    assert task.depends_on == (dependency,)
    assert task.constraints == ("safe",)
    with pytest.raises(ValidationError):
        UserContext.model_validate({**context.model_dump(), "extra": True})


def test_tool_definition_validates_name_version_timeout_and_approval() -> None:
    assert _definition().required_permissions == ("knowledge:read",)
    with pytest.raises(ValidationError):
        _definition(name="Invalid")
    with pytest.raises(ValidationError):
        _definition(version="1.0")
    with pytest.raises(ValidationError):
        _definition(timeout_ms=99)
    with pytest.raises(ValidationError, match="require approval"):
        _definition(risk_level="r2", requires_approval=False)

    internal = _definition(
        name="governance.write_audit", module="m4", risk_level="r2",
        visibility="runtime_internal", requires_approval=False,
    )
    assert internal.visibility == "runtime_internal"


def test_route_and_agent_steps_enforce_bounds() -> None:
    route = RouteDecision(
        target_agent="service", confidence=Decimal("0.8000"), source="rule",
        reason_code="SERVICE_INTENT", candidate_agents=["service", "knowledge", "service"],
    )
    assert route.candidate_agents == ("knowledge", "service")
    with pytest.raises(ValidationError):
        RouteDecision(
            target_agent="service", confidence=Decimal("1.1"), source="rule",
            reason_code="BAD",
        )
    with pytest.raises(ValidationError):
        AgentTask(
            task_id=uuid4(), agent_run_id=uuid4(), target_agent="service_agent",
            objective="task", max_steps=7,
        )


def test_sensitive_tool_fields_do_not_appear_in_repr() -> None:
    request = ToolCallRequest(
        agent_run_id=uuid4(), step_id=uuid4(), tool_name="knowledge.search",
        tool_version="1.0.0", arguments={"password": "secret"},
        idempotency_key="private-key", approval_id=uuid4(),
    )
    rendered = repr(request)
    assert "secret" not in rendered
    assert "private-key" not in rendered
    assert str(request.approval_id) not in rendered


def test_approval_lifecycle_requires_consistent_decision_fields() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    base = {
        "approval_id": uuid4(), "agent_run_id": uuid4(), "tool_call_id": uuid4(),
        "user_id": uuid4(), "action": "work_order.create",
        "display_summary": "Create work order", "arguments_hash": "a" * 64,
        "expires_at": now + timedelta(minutes=10), "created_at": now,
    }
    pending = ApprovalRequest(status="pending", **base)
    assert pending.status == "pending"
    with pytest.raises(ValidationError):
        ApprovalRequest(status="approved", **base)
    with pytest.raises(ValidationError):
        ApprovalRequest(status="pending", expires_at=now, **{k: v for k, v in base.items() if k != "expires_at"})
