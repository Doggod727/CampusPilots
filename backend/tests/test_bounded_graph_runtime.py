import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.domain.contracts import AgentResult, RouteDecision, ToolCallRequest, UserContext
from app.modules.agent_platform.orchestration.runtime import BoundedGraphRuntime, DeterministicMockSpecialist, InMemoryRuntimeCheckpointStore, InMemoryRuntimeEventSink, SpecialistOutcome
from app.modules.agent_platform.tool_gateway.errors import ToolApprovalRequired
from app.modules.agent_platform.traces import AgentRunStateConflict

RUN=uuid4(); USER=UserContext(user_id=uuid4(),username="student01",permissions=(),request_id="runtime-request")


def components(target="service"):
    router=MagicMock(); router.route=AsyncMock(return_value=RouteDecision(target_agent=target,confidence=Decimal("0.9"),source="rule",reason_code="ROUTE_RULE_SINGLE"))
    task=MagicMock(); task.target_agent="service_agent"; task.structured_input={}; task.task_id=uuid4()
    plan=MagicMock(); plan.status="ready"; plan.tasks=(task,)
    planner=MagicMock(); planner.plan.return_value=plan
    trace=MagicMock(); trace.transition_run=AsyncMock(); trace.append_step=AsyncMock(return_value=MagicMock(id=uuid4())); trace.transition_step=AsyncMock(); trace.transition_tool=AsyncMock(); trace.finalize=AsyncMock()
    events=InMemoryRuntimeEventSink(); specialist=DeterministicMockSpecialist("service_agent")
    return router,planner,trace,events,specialist


def test_runtime_routes_executes_and_finishes_with_monotonic_events() -> None:
    router,planner,trace,events,specialist=components()
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events)
    asyncio.run(runtime.start(RUN,USER,"电费查询",{}))
    assert [item.sequence for item in events.list(RUN)]==[1,2,3]
    assert events.list(RUN)[-1].data["status"]=="succeeded"
    trace.finalize.assert_awaited_once_with(RUN,"succeeded",finish_reason="completed")


def test_clarification_finishes_partial_without_specialist() -> None:
    router,planner,trace,events,_=components("clarify"); plan=MagicMock(status="needs_input",tasks=()); planner.plan.return_value=plan
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={},trace=trace,events=events)
    asyncio.run(runtime.start(RUN,USER,"?",{}))
    trace.append_step.assert_not_called(); trace.finalize.assert_awaited_once_with(RUN,"partial",finish_reason="clarification_required")


def test_duplicate_start_terminal_resume_and_cancel_boundaries() -> None:
    router,planner,trace,events,specialist=components(); runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events)
    asyncio.run(runtime.start(RUN,USER,"电费",{}))
    with pytest.raises(AgentRunStateConflict): asyncio.run(runtime.start(RUN,USER,"电费",{}))
    with pytest.raises(AgentRunStateConflict): asyncio.run(runtime.resume(RUN,uuid4()))
    asyncio.run(runtime.cancel(RUN)); assert trace.finalize.await_count==1


def test_event_sequences_are_independent_per_run() -> None:
    sink=InMemoryRuntimeEventSink(); other=uuid4()
    asyncio.run(sink.publish(RUN,"meta",{})); asyncio.run(sink.publish(RUN,"route",{})); asyncio.run(sink.publish(other,"meta",{}))
    assert [e.sequence for e in sink.list(RUN)]==[1,2] and sink.list(other)[0].sequence==1


def test_required_approval_pauses_before_runtime_can_finish() -> None:
    router,planner,trace,events,_=components(); step_id=uuid4(); call_id=uuid4(); approval_id=uuid4()
    trace.append_step.return_value=MagicMock(id=step_id); trace.append_tool.return_value=MagicMock(id=call_id)
    request=ToolCallRequest(agent_run_id=RUN,step_id=step_id,tool_name="electricity.create_topup_request",tool_version="1.0.0",arguments={"room_id":str(uuid4()),"amount_cny":"10.00"},idempotency_key="idem-1")
    specialist=MagicMock(); specialist.invoke=AsyncMock(return_value=SpecialistOutcome(AgentResult(task_id=planner.plan.return_value.tasks[0].task_id,agent_code="service_agent",status="succeeded",summary="prepared"),request))
    executor=MagicMock(); executor.prepare.return_value=MagicMock(arguments_hash="a"*64); executor.execute=AsyncMock(side_effect=ToolApprovalRequired())
    approvals=MagicMock(); approvals.create=AsyncMock(return_value=MagicMock(id=approval_id))
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("electricity.create_topup_request",)})
    asyncio.run(runtime.start(RUN,USER,"充值",{}))
    assert events.list(RUN)[-1].event=="approval_required"
    approvals.create.assert_awaited_once(); trace.finalize.assert_not_awaited()


def test_another_runtime_instance_can_resume_from_checkpoint() -> None:
    router,planner,trace,events,_=components(); step_id=uuid4(); call_id=uuid4(); approval_id=uuid4()
    trace.append_step.return_value=MagicMock(id=step_id); trace.append_tool.return_value=MagicMock(id=call_id)
    request=ToolCallRequest(agent_run_id=RUN,step_id=step_id,tool_name="electricity.create_topup_request",tool_version="1.0.0",arguments={"room_id":str(uuid4()),"amount_cny":"10.00"},idempotency_key="idem-1")
    specialist=MagicMock(); specialist.invoke=AsyncMock(return_value=SpecialistOutcome(AgentResult(task_id=planner.plan.return_value.tasks[0].task_id,agent_code="service_agent",status="succeeded",summary="prepared"),request))
    executor=MagicMock(); executor.prepare.return_value=MagicMock(arguments_hash="a"*64); executor.execute=AsyncMock(side_effect=[ToolApprovalRequired(),MagicMock(status="succeeded",data={"status":"simulated"},duration_ms=1,audit_id=None)])
    approvals=MagicMock(); approvals.create=AsyncMock(return_value=MagicMock(id=approval_id)); checkpoints=InMemoryRuntimeCheckpointStore()
    first=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("electricity.create_topup_request",)},checkpoints=checkpoints)
    asyncio.run(first.start(RUN,USER,"充值",{}))
    second=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("electricity.create_topup_request",)},checkpoints=checkpoints)
    asyncio.run(second.resume(RUN,approval_id))
    assert events.list(RUN)[-1].data["status"]=="succeeded" and executor.execute.await_count==2
