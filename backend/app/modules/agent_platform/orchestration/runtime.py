from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.agent_platform.approvals import ApprovalService
from app.modules.agent_platform.domain.contracts import (
    AgentResult, AgentTask, SupervisorPlan, ToolCallRequest, UserContext,
)
from app.modules.agent_platform.orchestration.router import RouterService
from app.modules.agent_platform.orchestration.supervisor import SupervisorPlanner
from app.modules.agent_platform.tool_gateway.errors import ToolApprovalRequired
from app.modules.agent_platform.tool_gateway.executor import ToolExecutor
from app.modules.agent_platform.traces import AgentRunStateConflict, TraceService


@dataclass(frozen=True)
class RuntimeEvent:
    run_id: UUID
    sequence: int
    event: str
    occurred_at: datetime
    data: dict[str, Any]


class RuntimeEventSinkPort(Protocol):
    async def publish(self, run_id: UUID, event: str, data: Mapping[str, Any]) -> RuntimeEvent: ...


class RuntimeCheckpointPort(Protocol):
    async def load(self, run_id: UUID) -> "RuntimeCheckpoint | None": ...
    async def save(self, run_id: UUID, state: "RuntimeCheckpoint") -> None: ...
    async def delete(self, run_id: UUID) -> None: ...


class SpecialistAgentPort(Protocol):
    async def invoke(self, task: AgentTask, user: UserContext) -> "SpecialistOutcome": ...


class RuntimeDispatcherPort(Protocol):
    async def start(self, run_id: UUID, user: UserContext, objective: str, context: Mapping[str, Any]) -> None: ...
    async def resume(self, run_id: UUID, approval_id: UUID) -> None: ...
    async def cancel(self, run_id: UUID) -> None: ...


class AgentSafetyPort(Protocol):
    async def check_input(self, user: UserContext, text: str, context: Mapping[str, Any]) -> tuple[str, dict[str, Any]]: ...
    async def check_output(self, user: UserContext, result: AgentResult) -> AgentResult: ...


@dataclass(frozen=True)
class SpecialistOutcome:
    result: AgentResult
    tool_request: ToolCallRequest | None = None


@dataclass
class RuntimeCheckpoint:
    user: UserContext = field(repr=False)
    objective: str = field(repr=False)
    context: dict[str, Any] = field(repr=False)
    plan: SupervisorPlan = field(repr=False)
    next_task: int = 0
    pending_step_id: UUID | None = None
    pending_tool_call_id: UUID | None = None
    pending_request: ToolCallRequest | None = field(default=None, repr=False)
    pending_agent_code: str | None = None
    terminal: bool = False
    had_failures: bool = False
    checkpoint_version: int = 0


class InMemoryRuntimeCheckpointStore:
    def __init__(self) -> None:
        self._states: dict[UUID, RuntimeCheckpoint] = {}

    async def load(self, run_id: UUID) -> RuntimeCheckpoint | None:
        return self._states.get(run_id)

    async def save(self, run_id: UUID, state: RuntimeCheckpoint) -> None:
        current = self._states.get(run_id)
        if current is not None and current is not state and current.checkpoint_version != state.checkpoint_version:
            raise AgentRunStateConflict()
        state.checkpoint_version += 1
        self._states[run_id] = state

    async def delete(self, run_id: UUID) -> None:
        self._states.pop(run_id, None)


class InMemoryRuntimeEventSink:
    def __init__(self) -> None:
        self._events: dict[UUID, list[RuntimeEvent]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, run_id: UUID, event: str, data: Mapping[str, Any]) -> RuntimeEvent:
        async with self._lock:
            event_item = RuntimeEvent(run_id, len(self._events[run_id]) + 1, event, datetime.now(UTC), dict(data))
            self._events[run_id].append(event_item)
            return event_item

    def list(self, run_id: UUID) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events.get(run_id, ()))


class DeterministicMockSpecialist:
    def __init__(self, agent_code: str) -> None: self._agent_code = agent_code
    async def invoke(self, task: AgentTask, user: UserContext) -> SpecialistOutcome:
        return SpecialistOutcome(AgentResult(
            task_id=task.task_id, agent_code=self._agent_code,
            status="succeeded", summary=f"{self._agent_code} mock completed",
            structured_output={"answer": f"mock:{task.objective[:120]}"},
        ))


class AllowAgentSafety:
    async def check_input(self, user: UserContext, text: str, context: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        return text, dict(context)
    async def check_output(self, user: UserContext, result: AgentResult) -> AgentResult:
        return result


class BoundedGraphRuntime:
    ACTIVE = {"created", "routing", "running", "awaiting_approval"}

    def __init__(self, *, router: RouterService, planner: SupervisorPlanner,
                 specialists: Mapping[str, SpecialistAgentPort], trace: TraceService,
                 events: RuntimeEventSinkPort, tool_executor: ToolExecutor | None = None,
                 approval_service: ApprovalService | None = None,
                 agent_allowlists: Mapping[str, tuple[str, ...]] | None = None,
                 safety: AgentSafetyPort | None = None,
                 checkpoints: RuntimeCheckpointPort | None = None) -> None:
        self._router=router; self._planner=planner; self._specialists=dict(specialists)
        self._trace=trace; self._events=events; self._tools=tool_executor; self._approvals=approval_service
        self._agent_allowlists=dict(agent_allowlists or {})
        self._safety=safety or AllowAgentSafety()
        self._checkpoints=checkpoints or InMemoryRuntimeCheckpointStore()
        self._handled_runs: set[UUID] = set()
        self._terminal_runs: set[UUID] = set()

    async def start(self, run_id: UUID, user: UserContext, objective: str, context: Mapping[str, Any]) -> None:
        if run_id in self._handled_runs or await self._checkpoints.load(run_id) is not None:
            raise AgentRunStateConflict()
        self._handled_runs.add(run_id)
        objective, safe_context = await self._safety.check_input(user, objective, context)
        await self._trace.transition_run(run_id, {"created"}, "routing", started_at=datetime.now(UTC))
        route=await self._router.route(objective)
        await self._events.publish(run_id,"route",route.model_dump(mode="json"))
        plan=self._planner.plan(agent_run_id=run_id,route=route,objective=objective,structured_input=safe_context)
        checkpoint=RuntimeCheckpoint(user,objective,dict(safe_context),plan)
        await self._checkpoints.save(run_id,checkpoint)
        if plan.status=="needs_input":
            await self._trace.finalize(run_id,"partial",finish_reason="clarification_required")
            checkpoint.terminal=True
            self._terminal_runs.add(run_id)
            await self._checkpoints.save(run_id,checkpoint)
            await self._events.publish(run_id,"done",{"status":"partial","reason":"clarification_required"})
            await self._checkpoints.delete(run_id)
            return
        await self._trace.transition_run(run_id,{"routing"},"running",route_decision=route.model_dump(mode="json"))
        await self._continue(run_id)

    async def resume(self, run_id: UUID, approval_id: UUID) -> None:
        state=await self._checkpoints.load(run_id)
        if state is None or state.terminal or state.pending_request is None or state.pending_step_id is None:
            raise AgentRunStateConflict()
        request=state.pending_request.model_copy(update={"approval_id":approval_id})
        await self._trace.transition_run(run_id,{"awaiting_approval"},"running")
        await self._execute_tool(state,state.pending_step_id,state.pending_tool_call_id,request,state.pending_agent_code or "")
        state.pending_request=None; state.pending_step_id=None; state.pending_tool_call_id=None; state.pending_agent_code=None; state.next_task+=1
        await self._checkpoints.save(run_id,state)
        await self._continue(run_id)

    async def cancel(self, run_id: UUID) -> None:
        if run_id in self._terminal_runs:
            return
        state=await self._checkpoints.load(run_id)
        if state is not None and state.terminal:
            return
        await self._trace.finalize(run_id,"cancelled",finish_reason="user_cancelled")
        if state is not None: state.terminal=True
        self._terminal_runs.add(run_id)
        if state is not None: await self._checkpoints.save(run_id,state)
        await self._events.publish(run_id,"done",{"status":"cancelled"})
        await self._checkpoints.delete(run_id)

    async def _continue(self, run_id: UUID) -> None:
        state=await self._checkpoints.load(run_id)
        if state is None:
            raise AgentRunStateConflict()
        while state.next_task < len(state.plan.tasks):
            task=state.plan.tasks[state.next_task]
            specialist=self._specialists.get(task.target_agent)
            if specialist is None:
                await self._trace.finalize(run_id,"failed",error_code="AGENT_NOT_FOUND")
                state.terminal=True; self._terminal_runs.add(run_id); await self._checkpoints.save(run_id,state); await self._checkpoints.delete(run_id); return
            step=await self._trace.append_step(run_id=run_id,agent_code=task.target_agent,task_type="generate",input_summary=task.structured_input)
            await self._trace.transition_step(step.id,{"created"},"running",started_at=datetime.now(UTC))
            outcome=await specialist.invoke(task,state.user)
            outcome=SpecialistOutcome(await self._safety.check_output(state.user,outcome.result),outcome.tool_request)
            if outcome.tool_request is not None:
                request=outcome.tool_request.model_copy(update={"agent_run_id":run_id,"step_id":step.id})
                prepared=self._tools.prepare(request) if self._tools else None
                call=self._trace.append_tool(run_id=run_id,step_id=step.id,tool_name=request.tool_name,tool_version=request.tool_version,arguments_hash=prepared.arguments_hash if prepared else "0"*64,arguments_summary=request.arguments,idempotency_key=request.idempotency_key)
                try:
                    await self._execute_tool(state,step.id,call.id,request,task.target_agent)
                except ToolApprovalRequired:
                    if self._approvals is None or prepared is None:
                        raise
                    approval=await self._approvals.create(run_id=run_id,tool_call_id=call.id,user_id=state.user.user_id,action=request.tool_name,display_summary=f"确认执行 {request.tool_name}",arguments_hash=prepared.arguments_hash)
                    await self._trace.transition_tool(call.id,{"prepared"},"awaiting_approval")
                    await self._trace.transition_step(step.id,{"running"},"awaiting_approval")
                    await self._trace.transition_run(run_id,{"running"},"awaiting_approval")
                    state.pending_request=request; state.pending_step_id=step.id; state.pending_tool_call_id=call.id; state.pending_agent_code=task.target_agent
                    await self._checkpoints.save(run_id,state)
                    await self._events.publish(run_id,"approval_required",{"approval_id":str(approval.id),"tool_name":request.tool_name})
                    return
            else:
                await self._trace.transition_step(step.id,{"running"},outcome.result.status if outcome.result.status in {"succeeded","partial","failed"} else "partial",output_summary=outcome.result.structured_output,finished_at=datetime.now(UTC),error_code=outcome.result.error.reason if outcome.result.error else None)
                state.had_failures = state.had_failures or outcome.result.status in {"partial", "failed"}
            state.next_task+=1
            await self._checkpoints.save(run_id,state)
            await self._events.publish(run_id,"agent_step",{"sequence":state.next_task,"agent_code":task.target_agent,"status":outcome.result.status})
        final_status = "partial" if state.had_failures else "succeeded"
        await self._trace.finalize(run_id,final_status,finish_reason="completed")
        state.terminal=True
        self._terminal_runs.add(run_id)
        await self._checkpoints.save(run_id,state)
        await self._events.publish(run_id,"done",{"status":final_status})
        await self._checkpoints.delete(run_id)

    async def _execute_tool(self,state:RuntimeCheckpoint,step_id:UUID,call_id:UUID|None,request:ToolCallRequest,agent_code:str) -> None:
        if self._tools is None or call_id is None: raise AgentRunStateConflict()
        allowlist=self._agent_allowlists.get(agent_code, ())
        result=await self._tools.execute(context=state.user,request=request,agent_allowlist=allowlist)
        await self._trace.transition_tool(call_id,{"prepared","awaiting_approval"},result.status,result_summary=result.data or {},duration_ms=result.duration_ms,finished_at=datetime.now(UTC),audit_id=result.audit_id)
        await self._trace.transition_step(step_id,{"running","awaiting_approval"},"succeeded",output_summary=result.data or {},finished_at=datetime.now(UTC))


class InProcessRuntimeDispatcher:
    def __init__(self, runtime: BoundedGraphRuntime) -> None: self._runtime=runtime
    async def start(self, run_id, user, objective, context): await self._runtime.start(run_id,user,objective,context)
    async def resume(self, run_id, approval_id): await self._runtime.resume(run_id,approval_id)
    async def cancel(self, run_id): await self._runtime.cancel(run_id)


@dataclass(frozen=True)
class RuntimeCommand:
    action: str
    run_id: UUID
    approval_id: UUID | None = None


class InMemoryCommandDispatcher:
    """Demo command handoff; workers consume commands with fresh DB sessions."""
    def __init__(self) -> None: self.commands: list[RuntimeCommand] = []
    async def start(self, run_id, user, objective, context): self.commands.append(RuntimeCommand("start",run_id))
    async def resume(self, run_id, approval_id): self.commands.append(RuntimeCommand("resume",run_id,approval_id))
    async def cancel(self, run_id): self.commands.append(RuntimeCommand("cancel",run_id))
