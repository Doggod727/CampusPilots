from copy import deepcopy
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import RouteDecision, SupervisorPlan
from app.modules.agent_platform.orchestration.agent_registry import (
    AGENT_REGISTRATIONS,
    AgentRegistry,
)
from app.modules.agent_platform.orchestration.supervisor import SupervisorPlanner


RUN_ID = UUID("30000000-0000-4000-8000-000000000001")


def _route(target: str, candidates=()) -> RouteDecision:
    return RouteDecision.model_validate({
        "target_agent": target,
        "confidence": Decimal("0.9000"),
        "source": "rule",
        "reason_code": "ROUTE_TEST",
        "candidate_agents": candidates,
    })


def _planner(**kwargs) -> SupervisorPlanner:
    return SupervisorPlanner(
        registry=AgentRegistry(AGENT_REGISTRATIONS), **kwargs
    )


def test_single_domain_creates_one_deterministic_task() -> None:
    planner = _planner()
    source = {"query": "宿舍报修", "safe": {"campus": "main"}}
    original = deepcopy(source)
    left = planner.plan(
        agent_run_id=RUN_ID, route=_route("service"),
        objective="处理校园服务请求", structured_input=source,
        constraints=("只使用授权资源",),
    )
    right = planner.plan(
        agent_run_id=RUN_ID, route=_route("service"),
        objective="处理校园服务请求", structured_input=source,
        constraints=("只使用授权资源",),
    )
    assert left == right
    assert left.status == "ready"
    assert len(left.tasks) == 1
    assert left.tasks[0].target_agent == "service_agent"
    assert left.tasks[0].depends_on == ()
    assert left.tasks[0].parent_task_id is None
    assert source == original
    assert "structured_input" not in repr(left.tasks[0])


def test_cross_domain_plan_is_unique_bounded_and_sequential() -> None:
    plan = _planner().plan(
        agent_run_id=RUN_ID,
        route=_route(
            "service", ("service", "community", "knowledge")
        ),
        objective="先查报修，再查活动和知识",
    )
    assert [task.target_agent for task in plan.tasks] == [
        "service_agent", "community_agent", "knowledge_agent"
    ]
    assert len({task.target_agent for task in plan.tasks}) == 3
    for index, task in enumerate(plan.tasks):
        if index == 0:
            assert task.depends_on == ()
            assert task.parent_task_id is None
        else:
            assert task.depends_on == (plan.tasks[index - 1].task_id,)
            assert task.parent_task_id == plan.tasks[index - 1].task_id


def test_clarify_returns_needs_input_without_tasks() -> None:
    plan = _planner().plan(
        agent_run_id=RUN_ID,
        route=_route("clarify"),
        objective="请处理一下",
    )
    assert plan.status == "needs_input"
    assert plan.tasks == ()
    assert plan.reason_code == "SUPERVISOR_NEEDS_INPUT"


def test_disabled_target_agent_is_rejected_before_plan_creation() -> None:
    disabled = AGENT_REGISTRATIONS[2].model_copy(update={
        "definition": AGENT_REGISTRATIONS[2].definition.model_copy(
            update={"enabled": False}
        )
    })
    registrations = [
        item for item in AGENT_REGISTRATIONS
        if item.definition.code != "service_agent"
    ] + [disabled]
    planner = SupervisorPlanner(registry=AgentRegistry(registrations))
    with pytest.raises(AppError) as error:
        planner.plan(
            agent_run_id=RUN_ID, route=_route("service"), objective="报修"
        )
    assert error.value.code == "AGENT_DISABLED"


@pytest.mark.parametrize("steps", [0, 7])
def test_requested_step_limit_uses_stable_error(steps: int) -> None:
    with pytest.raises(AppError) as error:
        _planner().plan(
            agent_run_id=RUN_ID, route=_route("knowledge"),
            objective="查询知识", max_steps=steps,
        )
    assert (error.value.status_code, error.value.code) == (
        409, "AGENT_MAX_STEPS_EXCEEDED"
    )


def test_supervisor_plan_contract_rejects_cycles_and_repeated_agents() -> None:
    valid = _planner().plan(
        agent_run_id=RUN_ID,
        route=_route("service", ("community",)),
        objective="组合任务",
    )
    repeated = valid.tasks[1].model_copy(update={
        "target_agent": valid.tasks[0].target_agent
    })
    with pytest.raises(ValidationError):
        SupervisorPlan(
            status="ready", route=valid.route,
            tasks=(valid.tasks[0], repeated), reason_code="INVALID_REPEAT",
        )
    cyclic = valid.tasks[0].model_copy(update={
        "depends_on": (valid.tasks[1].task_id,)
    })
    with pytest.raises(ValidationError):
        SupervisorPlan(
            status="ready", route=valid.route,
            tasks=(cyclic, valid.tasks[1]), reason_code="INVALID_CYCLE",
        )
