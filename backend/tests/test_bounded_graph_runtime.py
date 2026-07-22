import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.domain.contracts import AgentResult, AgentTask, RouteDecision, ToolCallRequest, UserContext
from app.modules.agent_platform.orchestration.runtime import BoundedGraphRuntime, DeterministicMockSpecialist, InMemoryRuntimeCheckpointStore, InMemoryRuntimeEventSink, SpecialistOutcome, _approval_display_summary, _multi_work_order_reply, _tool_success_answer
from app.modules.agent_platform.tool_gateway.errors import ToolApprovalRequired, ToolArgumentInvalid
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


def test_runtime_honors_explicit_agent_selection_without_calling_router() -> None:
    router,planner,trace,events,specialist=components()
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events)
    run_id=uuid4()
    asyncio.run(runtime.start(run_id,USER,"查询校园服务",{"requested_agent_codes":["service_agent"]}))
    router.route.assert_not_awaited()
    route=planner.plan.call_args.kwargs["route"]
    assert route.target_agent=="service" and route.reason_code=="ROUTE_EXPLICIT_AGENT_SELECTION"


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


def test_successful_tool_payloads_are_rendered_as_conversational_answers() -> None:
    topup = _tool_success_answer(
        "electricity.create_topup_request",
        {"amount": "50.00", "status": "simulated", "topup_request_id": str(uuid4())},
    )
    published = _tool_success_answer(
        "lost_found.publish",
        {"item_id": str(uuid4()), "status": "published"},
    )
    community_summary = _tool_success_answer(
        "community.topic.summarize",
        {"summary": "当前社区主要在讨论选课与食堂。", "items": [], "total": 2},
    )
    event_created = _tool_success_answer(
        "event.create", {"event_id": str(uuid4()), "status": "pending_review"},
    )

    assert topup == "已完成 50.00 元电费充值申请。这是模拟申请，不会产生真实扣款或到账。"
    assert published == "失物招领信息已发布成功。"
    assert community_summary == "当前社区主要在讨论选课与食堂。"
    assert "已创建并提交审核" in event_created
    assert not topup.lstrip().startswith("{")


def test_tool_approval_summary_is_a_user_facing_question() -> None:
    room_id = uuid4()
    summary = _approval_display_summary(
        "electricity.create_topup_request",
        {"room_id": str(room_id), "amount_cny": "50.00"},
    )

    assert summary == f"是否允许 CampusPilot 为您提交 50.00 元电费充值申请（房间 {room_id}）？"
    assert "electricity.create_topup_request" not in summary
    assert "创建并提交审核" in _approval_display_summary("event.create", {"title": "编程比赛"})
    assert "发布社区帖子" in _approval_display_summary("community.post.publish", {"title": "选课求助"})


def test_required_approval_pauses_before_runtime_can_finish() -> None:
    router,planner,trace,events,_=components(); step_id=uuid4(); call_id=uuid4(); approval_id=uuid4()
    trace.append_step.return_value=MagicMock(id=step_id); trace.append_tool.return_value=MagicMock(id=call_id)
    request=ToolCallRequest(agent_run_id=RUN,step_id=step_id,tool_name="electricity.create_topup_request",tool_version="1.0.0",arguments={"room_id":str(uuid4()),"amount_cny":"10.00"},idempotency_key="idem-1")
    specialist=MagicMock(); specialist.invoke=AsyncMock(return_value=SpecialistOutcome(AgentResult(task_id=planner.plan.return_value.tasks[0].task_id,agent_code="service_agent",status="succeeded",summary="prepared"),request))
    executor=MagicMock(); executor.prepare.return_value=MagicMock(arguments_hash="a"*64); executor.execute=AsyncMock(side_effect=ToolApprovalRequired())
    approvals=MagicMock(); approvals.create=AsyncMock(return_value=MagicMock(id=approval_id))
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("electricity.get_balance","electricity.create_topup_request")})
    asyncio.run(runtime.start(RUN,USER,"充值",{"requested_tool_names":["electricity.create_topup_request"]}))
    assert events.list(RUN)[-1].event=="approval_required"
    approvals.create.assert_awaited_once(); trace.finalize.assert_not_awaited()
    assert executor.execute.await_args.kwargs["agent_allowlist"]==("electricity.create_topup_request",)


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


def test_invalid_tool_arguments_after_approval_return_to_awaiting_input() -> None:
    router,planner,trace,events,_=components(); step_id=uuid4(); call_id=uuid4(); approval_id=uuid4()
    trace.append_step.return_value=MagicMock(id=step_id); trace.append_tool.return_value=MagicMock(id=call_id)
    request=ToolCallRequest(agent_run_id=RUN,step_id=step_id,tool_name="lost_found.publish",tool_version="1.0.0",arguments={"item_type":"lost","title":"手机","category":"手机","location":"体育场","occurred_at":"2025-04-04T15:00:00","description":"黑色小米手机"},idempotency_key="idem-1")
    specialist=MagicMock(); specialist.invoke=AsyncMock(return_value=SpecialistOutcome(AgentResult(task_id=planner.plan.return_value.tasks[0].task_id,agent_code="service_agent",status="succeeded",summary="prepared"),request))
    executor=MagicMock(); executor.prepare.return_value=MagicMock(arguments_hash="a"*64); executor.execute=AsyncMock(side_effect=[ToolApprovalRequired(),ToolArgumentInvalid()])
    approvals=MagicMock(); approvals.create=AsyncMock(return_value=MagicMock(id=approval_id)); checkpoints=InMemoryRuntimeCheckpointStore()
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("lost_found.publish",)},checkpoints=checkpoints)
    asyncio.run(runtime.start(RUN,USER,"发布寻物启事",{}))
    asyncio.run(runtime.resume(RUN,approval_id))
    assert events.list(RUN)[-1].event == "input_required"
    assert asyncio.run(checkpoints.load(RUN)) is not None
    failed_call = trace.transition_tool.await_args_list[-1]
    assert failed_call.args[:3] == (call_id,{"awaiting_approval","authorized","running"},"failed")
    assert failed_call.kwargs["error_code"] == "TOOL_ARGUMENT_INVALID"
    trace.finalize.assert_not_awaited()


def test_preflight_argument_error_does_not_create_approval_and_names_bad_field() -> None:
    router,planner,trace,events,_=components(); step_id=uuid4(); call_id=uuid4()
    trace.append_step.return_value=MagicMock(id=step_id); trace.append_tool.return_value=MagicMock(id=call_id)
    request=ToolCallRequest(agent_run_id=RUN,step_id=step_id,tool_name="work_order.create",tool_version="1.0.0",arguments={"room_id":str(uuid4()),"fault_type":"plumbing","description":"宿舍水龙头持续漏水，需要检修。"},idempotency_key="idem-1")
    specialist=MagicMock(); specialist.invoke=AsyncMock(return_value=SpecialistOutcome(AgentResult(task_id=planner.plan.return_value.tasks[0].task_id,agent_code="service_agent",status="succeeded",summary="prepared"),request))
    executor=MagicMock(); executor.prepare.return_value=MagicMock(arguments_hash="a"*64)
    executor.execute=AsyncMock(side_effect=ToolArgumentInvalid("可上门时间格式无效",field="available_time",reason="可上门时间须为‘ISO开始时间/ISO结束时间’"))
    approvals=MagicMock(); approvals.create=AsyncMock(); checkpoints=InMemoryRuntimeCheckpointStore()
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("work_order.create",)},checkpoints=checkpoints)

    asyncio.run(runtime.start(RUN,USER,"创建报修工单",{}))

    assert events.list(RUN)[-1].event == "input_required"
    assert "可上门时间" in events.list(RUN)[-1].data["message"]
    approvals.create.assert_not_awaited()
    assert asyncio.run(checkpoints.load(RUN)) is not None


def test_fabricated_write_success_is_retried_as_a_real_tool_call() -> None:
    router,planner,trace,events,_=components(); step_id=uuid4(); call_id=uuid4(); approval_id=uuid4()
    task = planner.plan.return_value.tasks[0]
    task.objective = "帮我创建报修工单"
    trace.append_step.return_value=MagicMock(id=step_id); trace.append_tool.return_value=MagicMock(id=call_id)
    fake = SpecialistOutcome(AgentResult(
        task_id=task.task_id,agent_code="service_agent",status="succeeded",
        summary="已创建工单 123",structured_output={"answer":"已创建工单 123"},
    ))
    request=ToolCallRequest(agent_run_id=RUN,step_id=step_id,tool_name="work_order.create",tool_version="1.0.0",arguments={"room_id":str(uuid4()),"fault_type":"plumbing","description":"宿舍水龙头持续漏水，需要检修。"})
    real = SpecialistOutcome(AgentResult(
        task_id=task.task_id,agent_code="service_agent",status="succeeded",
        summary="准备创建工单",structured_output={"answer":"准备提交"},
    ), request)
    specialist=MagicMock(); specialist.invoke=AsyncMock(side_effect=[fake,real])
    executor=MagicMock(); executor.prepare.return_value=MagicMock(arguments_hash="a"*64); executor.execute=AsyncMock(side_effect=ToolApprovalRequired())
    approvals=MagicMock(); approvals.create=AsyncMock(return_value=MagicMock(id=approval_id))
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("work_order.create",)})

    asyncio.run(runtime.start(RUN,USER,"帮我创建报修工单",{}))

    assert specialist.invoke.await_count == 2
    approvals.create.assert_awaited_once()
    assert events.list(RUN)[-1].event == "approval_required"


def test_repeated_fabricated_write_success_is_never_shown_as_completed() -> None:
    router,planner,trace,events,_=components(); step_id=uuid4()
    task = planner.plan.return_value.tasks[0]
    task.objective = "帮我创建报修工单"
    trace.append_step.return_value=MagicMock(id=step_id)
    fake = SpecialistOutcome(AgentResult(
        task_id=task.task_id,agent_code="service_agent",status="succeeded",
        summary="已创建两个工单",structured_output={"answer":"已创建两个工单"},
    ))
    specialist=MagicMock(); specialist.invoke=AsyncMock(side_effect=[fake,fake])
    executor=MagicMock(); approvals=MagicMock(); approvals.create=AsyncMock()
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,tool_executor=executor,approval_service=approvals,agent_allowlists={"service_agent":("work_order.create",)})

    asyncio.run(runtime.start(RUN,USER,"帮我创建报修工单",{}))

    assert events.list(RUN)[-1].event == "input_required"
    assert "本次没有创建工单" in events.list(RUN)[-1].data["message"]
    approvals.create.assert_not_awaited()


def test_multiple_work_orders_must_be_selected_one_at_a_time() -> None:
    task = AgentTask(
        task_id=uuid4(),agent_run_id=uuid4(),target_agent="service_agent",
        objective="创建报修工单",
        structured_input={"continuation_input":"分别创建两个，描述你可以自己扩展"},
    )
    assert _multi_work_order_reply(task) is True


def test_needs_input_keeps_checkpoint_and_continues_original_task() -> None:
    router,planner,trace,events,_=components()
    task = planner.plan.return_value.tasks[0]
    continued_task = MagicMock()
    continued_task.target_agent = "service_agent"
    continued_task.structured_input = {"continuation_input": "20000000-0000-4000-8000-000000000001"}
    continued_task.task_id = task.task_id
    task.model_copy.return_value = continued_task
    specialist=MagicMock()
    specialist.invoke=AsyncMock(side_effect=[
        SpecialistOutcome(AgentResult(task_id=task.task_id,agent_code="service_agent",status="needs_input",summary="请提供房间ID",structured_output={"answer":None,"missing_slots":["room_id"]})),
        SpecialistOutcome(AgentResult(task_id=task.task_id,agent_code="service_agent",status="succeeded",summary="查询完成",structured_output={"balance":"20.00"})),
    ])
    checkpoints=InMemoryRuntimeCheckpointStore()
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,checkpoints=checkpoints)
    asyncio.run(runtime.start(RUN,USER,"查询电费",{}))
    assert events.list(RUN)[-1].event == "input_required"
    assert asyncio.run(checkpoints.load(RUN)) is not None
    trace.finalize.assert_not_awaited()
    assert trace.transition_step.await_args_list[-1].kwargs["output_summary"]["answer"] == "请提供房间ID"
    asyncio.run(runtime.continue_input(RUN,"20000000-0000-4000-8000-000000000001"))
    assert events.list(RUN)[-1].data["status"] == "succeeded"
    assert asyncio.run(checkpoints.load(RUN)) is None
    router.route.assert_awaited_once()
    assert specialist.invoke.await_count == 2


def test_electricity_continuation_binds_owned_uuid_to_room_id() -> None:
    router,planner,trace,events,_=components()
    planner.plan.return_value.route = RouteDecision(target_agent="service",confidence=Decimal("0.9"),source="deepseek",reason_code="SERVICE_QUERY")
    room_id = uuid4()
    user = USER.model_copy(update={"room_ids": (room_id,)})
    task = planner.plan.return_value.tasks[0]
    specialist=MagicMock()
    specialist.invoke=AsyncMock(side_effect=[
        SpecialistOutcome(AgentResult(task_id=task.task_id,agent_code="service_agent",status="needs_input",summary="请补充",structured_output={"missing_slots":["query"]})),
        SpecialistOutcome(AgentResult(task_id=task.task_id,agent_code="service_agent",status="succeeded",summary="完成")),
    ])
    checkpoints=InMemoryRuntimeCheckpointStore()
    runtime=BoundedGraphRuntime(router=router,planner=planner,specialists={"service_agent":specialist},trace=trace,events=events,checkpoints=checkpoints)
    asyncio.run(runtime.start(RUN,user,"查询电费",{}))
    asyncio.run(runtime.continue_input(RUN,str(room_id)))
    copied = task.model_copy.call_args.kwargs["update"]
    assert copied["structured_input"]["room_id"] == str(room_id)
    assert "query" not in copied["structured_input"]


def test_topup_amount_and_room_are_accumulated_across_continuation_turns() -> None:
    router,planner,trace,events,_=components()
    run_id, room_id = uuid4(), uuid4()
    task = AgentTask(
        task_id=uuid4(), agent_run_id=run_id, target_agent="service_agent",
        objective="帮我充电费",
        structured_input={"resolved_intent": "electricity.create_topup_request"},
    )
    planner.plan.return_value.tasks = (task,)
    specialist = MagicMock()
    specialist.invoke = AsyncMock(side_effect=[
        SpecialistOutcome(AgentResult(
            task_id=task.task_id, agent_code="service_agent", status="needs_input",
            summary="请提供充值金额。",
            structured_output={"answer": "请提供充值金额。", "missing_slots": ["amount_cny"]},
        )),
        SpecialistOutcome(AgentResult(
            task_id=task.task_id, agent_code="service_agent", status="needs_input",
            summary="请提供房间号。",
            structured_output={"answer": "请提供房间号。", "missing_slots": ["room_id"]},
        )),
    ])
    trace.append_step.side_effect = [MagicMock(id=uuid4()) for _ in range(3)]
    trace.append_tool.return_value = MagicMock(id=uuid4())
    executor = MagicMock()
    executor.prepare.return_value = MagicMock(arguments_hash="a" * 64)
    executor.execute = AsyncMock(side_effect=ToolApprovalRequired())
    approvals = MagicMock()
    approvals.create = AsyncMock(return_value=MagicMock(id=uuid4()))
    runtime = BoundedGraphRuntime(
        router=router, planner=planner, specialists={"service_agent": specialist},
        trace=trace, events=events, tool_executor=executor,
        approval_service=approvals,
        agent_allowlists={"service_agent": ("electricity.create_topup_request",)},
    )
    user = USER.model_copy(update={"room_ids": (room_id,)})

    asyncio.run(runtime.start(run_id, user, "帮我充电费", {}))
    asyncio.run(runtime.continue_input(run_id, "50元"))
    asyncio.run(runtime.continue_input(run_id, str(room_id)))

    prepared_request = executor.prepare.call_args.args[0]
    assert prepared_request.tool_name == "electricity.create_topup_request"
    assert prepared_request.arguments == {"room_id": str(room_id), "amount_cny": "50"}
    assert specialist.invoke.await_count == 2
    assert events.list(run_id)[-1].event == "approval_required"
    final_step_input = trace.append_step.call_args_list[-1].kwargs["input_summary"]
    assert final_step_input["continuation_history"] == ["50元", str(room_id)]
    assert final_step_input["amount_cny"] == "50"


def test_generic_tool_continuations_keep_all_prior_user_replies() -> None:
    router,planner,trace,events,_=components("community")
    run_id = uuid4()
    task = AgentTask(
        task_id=uuid4(), agent_run_id=run_id, target_agent="community_agent",
        objective="帮我发布寻物启事", structured_input={},
    )
    planner.plan.return_value.tasks = (task,)
    specialist = MagicMock()
    specialist.invoke = AsyncMock(side_effect=[
        SpecialistOutcome(AgentResult(
            task_id=task.task_id, agent_code="community_agent", status="needs_input",
            summary="请提供地点。",
            structured_output={"answer": "请提供地点。", "missing_slots": ["location"]},
        )),
        SpecialistOutcome(AgentResult(
            task_id=task.task_id, agent_code="community_agent", status="needs_input",
            summary="请提供分类。",
            structured_output={"answer": "请提供分类。", "missing_slots": ["category"]},
        )),
        SpecialistOutcome(AgentResult(
            task_id=task.task_id, agent_code="community_agent", status="succeeded",
            summary="信息已补充完整。", structured_output={"answer": "信息已补充完整。"},
        )),
    ])
    trace.append_step.side_effect = [MagicMock(id=uuid4()) for _ in range(3)]
    runtime = BoundedGraphRuntime(
        router=router, planner=planner, specialists={"community_agent": specialist},
        trace=trace, events=events,
    )

    asyncio.run(runtime.start(run_id, USER, "帮我发布寻物启事", {}))
    asyncio.run(runtime.continue_input(run_id, "二号体育场东门看台"))
    asyncio.run(runtime.continue_input(run_id, "电子产品"))

    final_task = specialist.invoke.await_args_list[-1].args[0]
    assert final_task.structured_input["continuation_history"] == [
        "二号体育场东门看台", "电子产品",
    ]
    assert "第1次补充：二号体育场东门看台" in final_task.objective
    assert "第2次补充：电子产品" in final_task.objective


def test_continuation_history_is_isolated_between_planned_tasks() -> None:
    router,planner,trace,events,_=components("community")
    run_id = uuid4()
    first = AgentTask(
        task_id=uuid4(), agent_run_id=run_id, target_agent="community_agent",
        objective="第一个社区任务", structured_input={},
    )
    second = AgentTask(
        task_id=uuid4(), agent_run_id=run_id, target_agent="community_agent",
        objective="第二个社区任务", structured_input={},
    )
    planner.plan.return_value.tasks = (first, second)
    specialist = MagicMock()
    specialist.invoke = AsyncMock(side_effect=[
        SpecialistOutcome(AgentResult(
            task_id=first.task_id, agent_code="community_agent", status="needs_input",
            summary="补充第一项", structured_output={"missing_slots": ["query"]},
        )),
        SpecialistOutcome(AgentResult(
            task_id=first.task_id, agent_code="community_agent", status="succeeded",
            summary="第一项完成", structured_output={"answer": "第一项完成"},
        )),
        SpecialistOutcome(AgentResult(
            task_id=second.task_id, agent_code="community_agent", status="needs_input",
            summary="补充第二项", structured_output={"missing_slots": ["query"]},
        )),
        SpecialistOutcome(AgentResult(
            task_id=second.task_id, agent_code="community_agent", status="succeeded",
            summary="第二项完成", structured_output={"answer": "第二项完成"},
        )),
    ])
    trace.append_step.side_effect = [MagicMock(id=uuid4()) for _ in range(4)]
    runtime = BoundedGraphRuntime(
        router=router, planner=planner, specialists={"community_agent": specialist},
        trace=trace, events=events,
    )

    asyncio.run(runtime.start(run_id, USER, "依次完成两个社区任务", {}))
    asyncio.run(runtime.continue_input(run_id, "第一项的补充"))
    asyncio.run(runtime.continue_input(run_id, "第二项的补充"))

    first_continued = specialist.invoke.await_args_list[1].args[0]
    second_continued = specialist.invoke.await_args_list[3].args[0]
    assert first_continued.structured_input["continuation_history"] == ["第一项的补充"]
    assert second_continued.structured_input["continuation_history"] == ["第二项的补充"]
    assert "第一项的补充" not in second_continued.objective
