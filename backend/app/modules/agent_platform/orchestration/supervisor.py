from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.modules.agent_platform.domain.contracts import (
    AgentTask,
    RouteDecision,
    SupervisorPlan,
)
from app.modules.agent_platform.orchestration.agent_registry import AgentRegistry
from app.modules.agent_platform.orchestration.errors import AgentMaxStepsExceeded


_AGENT_BY_TARGET = {
    "knowledge": "knowledge_agent",
    "service": "service_agent",
    "community": "community_agent",
    "governance": "governance_agent",
    "modelops": "modelops_agent",
}


class SupervisorPlanner:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        max_steps: int = 6,
        max_specialists: int = 3,
    ) -> None:
        if not 1 <= max_steps <= 6:
            raise ValueError("max_steps must be between 1 and 6")
        if not 1 <= max_specialists <= 3:
            raise ValueError("max_specialists must be between 1 and 3")
        self._registry = registry
        self._max_steps = max_steps
        self._max_specialists = max_specialists

    def plan(
        self,
        *,
        agent_run_id: UUID,
        route: RouteDecision,
        objective: str,
        structured_input: Mapping[str, Any] | None = None,
        constraints: Iterable[str] = (),
        max_steps: int | None = None,
    ) -> SupervisorPlan:
        requested_steps = max_steps if max_steps is not None else self._max_steps
        if requested_steps < 1 or requested_steps > self._max_steps or requested_steps > 6:
            raise AgentMaxStepsExceeded()
        if route.target_agent == "clarify":
            return SupervisorPlan(
                status="needs_input",
                route=route,
                tasks=(),
                reason_code="SUPERVISOR_NEEDS_INPUT",
            )

        targets: list[str] = []
        for target in (route.target_agent, *route.candidate_agents):
            if target == "clarify" or target in targets:
                continue
            targets.append(target)
            if len(targets) == self._max_specialists:
                break

        tasks: list[AgentTask] = []
        previous_id: UUID | None = None
        safe_input = deepcopy(dict(structured_input or {}))
        for sequence, target in enumerate(targets, start=1):
            agent_code = _AGENT_BY_TARGET[target]
            self._registry.get_active(agent_code)
            task_id = uuid5(
                NAMESPACE_URL,
                f"campuspilot:agent-task:{agent_run_id}:{sequence}:{agent_code}",
            )
            tasks.append(AgentTask(
                task_id=task_id,
                agent_run_id=agent_run_id,
                parent_task_id=previous_id,
                target_agent=agent_code,
                objective=objective,
                structured_input=deepcopy(safe_input),
                depends_on=(previous_id,) if previous_id is not None else (),
                constraints=tuple(constraints),
                max_steps=requested_steps,
            ))
            previous_id = task_id

        return SupervisorPlan(
            status="ready",
            route=route,
            tasks=tuple(tasks),
            reason_code="SUPERVISOR_PLAN_READY",
        )
