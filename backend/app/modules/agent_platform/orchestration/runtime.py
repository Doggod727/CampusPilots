from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.modules.agent_platform.approvals import ApprovalService
from app.modules.agent_platform.domain.contracts import (
    AgentResult, AgentTask, RouteDecision, SupervisorPlan, ToolCallRequest, UserContext,
)
from app.modules.agent_platform.orchestration.router import RouterService
from app.modules.agent_platform.orchestration.supervisor import SupervisorPlanner
from app.modules.agent_platform.tool_gateway.errors import ToolApprovalRequired, ToolArgumentInvalid
from app.modules.agent_platform.tool_gateway.executor import ToolExecutor
from app.modules.agent_platform.traces import AgentRunStateConflict, TraceService


def _current_message(text: str) -> str:
    """objective 可能拼接了会话历史；意图判断只依据当前用户消息。"""

    if not isinstance(text, str):
        return ""
    return text.rsplit("当前用户消息：", 1)[-1]


_TOPUP_WORDS = ("充电费", "充值", "充钱", "缴电费", "缴费", "交电费", "交费")
_TOPUP_AMOUNT_RE = re.compile(r"充\s*\d+(?:\.\d+)?\s*元?")


def _is_electricity_topup_text(text: str) -> bool:
    return any(word in text for word in _TOPUP_WORDS) or bool(
        _TOPUP_AMOUNT_RE.search(text)
    )


def _required_write_tool(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    normalized = text.casefold()
    if _is_electricity_topup_text(normalized):
        return "electricity.create_topup_request"
    if any(word in normalized for word in ("创建报修", "提交报修", "创建工单", "帮我报修", "报修工单")):
        return "work_order.create"
    if "活动" in normalized and any(word in normalized for word in ("创建", "发布", "发起")):
        return "event.create"
    if any(word in normalized for word in ("寻物启事", "失物招领")) and any(
        word in normalized for word in ("发布", "帮我发", "创建")
    ):
        return "lost_found.publish"
    if any(word in normalized for word in ("社区帖子", "社区话题", "树洞")) and any(
        word in normalized for word in ("发布", "发帖", "帮我发", "创建")
    ):
        return "community.post.publish"
    if "报名" in normalized and "活动" in normalized:
        return "event.register"
    return None


def _multi_work_order_reply(task: AgentTask) -> bool:
    latest = task.structured_input.get("continuation_input")
    if not isinstance(latest, str):
        return False
    return any(word in latest for word in ("两个", "分别创建", "都创建", "各创建"))


def _honest_tool_required_outcome(task: AgentTask, tool_name: str) -> "SpecialistOutcome":
    if tool_name == "work_order.create":
        answer = "本次没有创建工单。当前一次只能创建一个工单，请先说明要创建的一个故障，例如“先创建水龙头漏水工单”；完成后再单独创建另一个。"
    else:
        answer = "本次操作尚未执行，因为系统没有生成有效的工具调用。请补充或更正信息后重试。"
    return SpecialistOutcome(AgentResult(
        task_id=task.task_id,
        agent_code=task.target_agent,
        status="needs_input",
        summary=answer,
        structured_output={"answer": answer, "missing_slots": []},
    ))


def _amount_from_text(text: str) -> str | None:
    match = re.search(
        r"(?:充值|充)(?:电费)?\s*(\d+(?:\.\d+)?)\s*元?|(?<![\d-])(\d+(?:\.\d+)?)\s*元",
        text,
    )
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _tool_success_answer(tool_name: str, data: Mapping[str, Any]) -> str:
    """Turn a successful tool payload into a conversational answer."""
    if tool_name == "knowledge.answer" and isinstance(data.get("answer"), str):
        return str(data["answer"])
    if tool_name == "knowledge.search":
        count = len(data.get("items") or ())
        return f"知识库检索已完成，共找到 {count} 条相关内容。" if count else "知识库检索已完成，暂未找到相关内容。"
    if tool_name == "service.get_guide":
        items = data.get("items") or ()
        if not items:
            return "办事指南查询已完成，暂未找到匹配的指南。"
        blocks: list[str] = []
        for item in items[:3]:
            if not isinstance(item, Mapping):
                continue
            lines = [f"【{item.get('title')}】{item.get('summary') or ''}".rstrip()]
            facts = []
            if item.get("department"):
                facts.append(f"办理部门：{item['department']}")
            if item.get("location"):
                facts.append(f"办理地点：{item['location']}")
            if item.get("service_hours"):
                facts.append(f"服务时间：{item['service_hours']}")
            if facts:
                lines.append("；".join(facts))
            materials = item.get("materials") or ()
            if materials:
                lines.append("所需材料：" + "、".join(str(m) for m in materials))
            steps = item.get("steps") or ()
            if steps:
                lines.append("办理步骤：" + "；".join(str(s) for s in steps))
            blocks.append("\n".join(lines))
        rest = [
            str(item.get("title"))
            for item in items[3:5]
            if isinstance(item, Mapping) and item.get("title")
        ]
        answer = "\n\n".join(blocks)
        if rest:
            answer += ("\n\n" if answer else "") + "其他相关指南：" + "、".join(rest) + "。"
        return answer or "办事指南查询已完成。"
    if tool_name == "work_order.create":
        identifier = data.get("work_order_id")
        return f"报修工单已提交，工单编号为 {identifier}。" if identifier else "报修工单已提交。"
    if tool_name == "work_order.get":
        status = data.get("status")
        return f"工单查询成功，当前状态为 {status}。" if status else "工单查询成功。"
    if tool_name == "electricity.get_balance":
        return f"查询成功，房间当前电费余额为 {data.get('balance')} {data.get('currency', 'CNY')}。"
    if tool_name == "electricity.create_topup_request":
        amount = data.get("amount")
        balance_after = data.get("balance_after")
        if amount is not None and balance_after is not None:
            return f"已成功充值 {amount} 元电费，当前余额为 {balance_after} 元。"
        if amount is not None:
            return f"已成功充值 {amount} 元电费。"
        return "电费充值已完成。"
    if tool_name == "event.search":
        items = data.get("items") or ()
        if not items:
            return "校园活动查询已完成，暂未找到匹配的活动。"
        titles = [str(item.get("title")) for item in items[:5] if isinstance(item, Mapping) and item.get("title")]
        return "已找到以下校园活动：" + "、".join(titles) + "。"
    if tool_name == "event.register":
        return "校园活动报名已完成。"
    if tool_name == "event.create":
        identifier = data.get("event_id")
        return f"校园活动已创建并提交审核，活动编号为 {identifier}。" if identifier else "校园活动已创建并提交审核。"
    if tool_name == "community.post.publish":
        status = data.get("status")
        return "社区帖子已发布。" if status == "published" else "社区帖子已提交，正在等待审核。"
    if tool_name == "community.topic.summarize":
        summary = data.get("summary")
        return str(summary) if summary else "社区话题查询与总结已完成。"
    if tool_name == "lost_found.publish":
        return "失物招领信息已发布成功。"
    if tool_name == "lost_found.search_matches":
        count = len(data.get("matches") or ())
        return f"匹配查询已完成，共找到 {count} 条可能相关的信息。" if count else "匹配查询已完成，暂未找到可能相关的信息。"
    return "操作已成功完成。"


def _approval_display_summary(tool_name: str, arguments: Mapping[str, Any]) -> str:
    if tool_name == "electricity.create_topup_request":
        amount = arguments.get("amount_cny")
        room_id = arguments.get("room_id")
        amount_text = f" {amount} 元" if amount is not None else ""
        room_text = f"（房间 {room_id}）" if room_id else ""
        return f"是否允许 CampusPilot 为您提交{amount_text}电费充值申请{room_text}？"
    if tool_name == "work_order.create":
        fault_type = arguments.get("fault_type")
        suffix = f"（{fault_type}）" if fault_type else ""
        return f"是否允许 CampusPilot 为您提交报修工单{suffix}？"
    if tool_name == "event.register":
        return "是否允许 CampusPilot 为您报名该校园活动？"
    if tool_name == "event.create":
        title = arguments.get("title")
        suffix = f"“{title}”" if title else "这个校园活动"
        return f"是否允许 CampusPilot 为您创建并提交审核{suffix}？"
    if tool_name == "community.post.publish":
        title = arguments.get("title")
        suffix = f"“{title}”" if title else "这篇帖子"
        return f"是否允许 CampusPilot 为您发布社区帖子{suffix}？"
    if tool_name == "lost_found.publish":
        title = arguments.get("title")
        suffix = f"“{title}”" if title else "这条信息"
        return f"是否允许 CampusPilot 为您发布{suffix}？"
    return "是否允许 CampusPilot 执行这项操作？"


def _tool_argument_feedback(error: ToolArgumentInvalid) -> str:
    reasons = [detail.reason for detail in error.details if detail.reason]
    explanation = "；".join(dict.fromkeys(reasons)) or error.message
    return f"参数未通过校验：{explanation}。请补充或更正后重试。"


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
    async def continue_input(self, run_id: UUID, user_input: str) -> None: ...
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
    ACTIVE = {"created", "routing", "running", "awaiting_input", "awaiting_approval"}
    _TARGET_BY_AGENT = {
        "knowledge_agent": "knowledge",
        "service_agent": "service",
        "community_agent": "community",
        "governance_agent": "governance",
        "modelops_agent": "modelops",
    }

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
        requested_agents = tuple(safe_context.get("requested_agent_codes") or ())
        if requested_agents:
            targets = tuple(self._TARGET_BY_AGENT[code] for code in requested_agents)
            route = RouteDecision(
                target_agent=targets[0],
                confidence=Decimal("1"),
                source="rule",
                reason_code="ROUTE_EXPLICIT_AGENT_SELECTION",
                candidate_agents=targets[1:],
            )
        else:
            route=await self._router.route(objective)
        await self._events.publish(run_id,"route",route.model_dump(mode="json"))
        current_message = _current_message(objective)
        if (
            route.target_agent == "service"
            and ("电费" in current_message or "ELECTRICITY" in route.reason_code)
            and not _is_electricity_topup_text(current_message)
        ):
            # Persist the cross-turn intent separately from the free-form text.
            # A later UUID must fill room_id, not trigger a fresh classification.
            safe_context["resolved_intent"] = "electricity.get_balance"
        elif route.target_agent == "service" and _is_electricity_topup_text(current_message):
            safe_context["resolved_intent"] = "electricity.create_topup_request"
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
        if state is None or state.terminal or state.pending_request is None or state.pending_step_id is None or state.pending_tool_call_id is None:
            raise AgentRunStateConflict()
        request=state.pending_request.model_copy(update={"approval_id":approval_id})
        await self._trace.transition_run(run_id,{"awaiting_approval"},"running")
        try:
            await self._execute_tool(state,state.pending_step_id,state.pending_tool_call_id,request,state.pending_agent_code or "")
        except ToolArgumentInvalid as exc:
            # Approval has already been consumed by the executor. Keep the run
            # resumable so corrected input creates a fresh, separately approved
            # tool call instead of turning the next message into an unrelated run.
            await self._trace.transition_tool(
                state.pending_tool_call_id,{"awaiting_approval","authorized","running"},"failed",
                result_summary={},error_code=exc.code,finished_at=datetime.now(UTC),
            )
            message = _tool_argument_feedback(exc)
            await self._trace.transition_step(
                state.pending_step_id,{"awaiting_approval","running"},"partial",
                output_summary={"answer":message},error_code=exc.code,finished_at=datetime.now(UTC),
            )
            state.pending_request=None; state.pending_step_id=None; state.pending_tool_call_id=None; state.pending_agent_code=None
            await self._trace.transition_run(run_id,{"running"},"awaiting_input")
            await self._checkpoints.save(run_id,state)
            await self._events.publish(run_id,"input_required",{"status":"awaiting_input","message":message})
            return
        state.pending_request=None; state.pending_step_id=None; state.pending_tool_call_id=None; state.pending_agent_code=None; state.next_task+=1
        await self._checkpoints.save(run_id,state)
        await self._continue(run_id)

    async def continue_input(self, run_id: UUID, user_input: str) -> None:
        state = await self._checkpoints.load(run_id)
        if state is None or state.terminal or not user_input.strip():
            raise AgentRunStateConflict()
        reply, _ = await self._safety.check_input(state.user, user_input, {})
        state.context["_continuation_input"] = reply
        if state.next_task >= len(state.plan.tasks):
            raise AgentRunStateConflict()
        task_key = str(state.plan.tasks[state.next_task].task_id)
        histories = dict(state.context.get("_continuation_histories", {}))
        task_history = list(histories.get(task_key, []))
        task_history.append(reply)
        histories[task_key] = task_history
        state.context["_continuation_histories"] = histories
        missing_slots = tuple(state.context.get("_missing_slots") or ())
        try:
            supplied_uuid = UUID(reply.strip())
        except ValueError:
            supplied_uuid = None
        resolved_slots: dict[str, str] = {}
        amount = _amount_from_text(reply)
        if "amount_cny" in missing_slots and amount is not None:
            resolved_slots["amount_cny"] = amount
        if (
            supplied_uuid is not None
            and supplied_uuid in state.user.room_ids
            and state.plan.route.target_agent == "service"
        ):
            resolved_slots["room_id"] = str(supplied_uuid)
            # The authenticated room scope makes this UUID unambiguous. Without
            # an explicit top-up request, continuing as a read-only query is safe.
            if not _is_electricity_topup_text(_current_message(state.objective)):
                resolved_slots["resolved_intent"] = "electricity.get_balance"
        elif supplied_uuid is not None and len(missing_slots) == 1:
            resolved_slots[str(missing_slots[0])] = str(supplied_uuid)
        if resolved_slots:
            state.context["_resolved_slots"] = resolved_slots
        await self._trace.transition_run(run_id, {"awaiting_input"}, "running")
        await self._checkpoints.save(run_id, state)
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
            continuation = state.context.pop("_continuation_input", None)
            if isinstance(continuation, str):
                task_key = str(task.task_id)
                resolved_slots = dict(state.context.pop("_resolved_slots", {}))
                collected_by_task = dict(state.context.get("_collected_slots_by_task", {}))
                collected_slots = dict(collected_by_task.get(task_key, {}))
                collected_slots.update(resolved_slots)
                collected_by_task[task_key] = collected_slots
                state.context["_collected_slots_by_task"] = collected_by_task
                histories = dict(state.context.get("_continuation_histories", {}))
                continuation_history = list(histories.get(task_key, [continuation]))
                history_text = "\n".join(
                    f"第{index}次补充：{value}"
                    for index, value in enumerate(continuation_history, start=1)
                )
                task = task.model_copy(update={
                    "objective": (
                        f"原始任务：{state.objective}\n"
                        f"用户历次补充信息：\n{history_text}\n"
                        f"已解析并验证的参数：{collected_slots}\n"
                        "请结合原始任务和全部补充信息继续同一工具操作，不得改换无关工具。"
                    ),
                    "structured_input": {
                        **task.structured_input,
                        "continuation_input": continuation,
                        "continuation_history": continuation_history,
                        **collected_slots,
                    },
                })
            specialist=self._specialists.get(task.target_agent)
            if specialist is None:
                await self._trace.finalize(run_id,"failed",error_code="AGENT_NOT_FOUND")
                state.terminal=True; self._terminal_runs.add(run_id); await self._checkpoints.save(run_id,state); await self._checkpoints.delete(run_id); return
            required_write_tool = _required_write_tool(_current_message(task.objective))
            step=await self._trace.append_step(run_id=run_id,agent_code=task.target_agent,task_type="generate",input_summary=task.structured_input)
            await self._trace.transition_step(step.id,{"created"},"running",started_at=datetime.now(UTC))
            if (
                task.target_agent == "service_agent"
                and task.structured_input.get("resolved_intent") == "electricity.get_balance"
                and isinstance(task.structured_input.get("room_id"), str)
            ):
                # Intent and ownership were established by the preceding turn;
                # avoid asking the model to classify the same information again.
                outcome = SpecialistOutcome(
                    AgentResult(
                        task_id=task.task_id,
                        agent_code="service_agent",
                        status="succeeded",
                        summary="已确认查询电费余额，正在查询。",
                        structured_output={"intent": "electricity.get_balance"},
                    ),
                    ToolCallRequest(
                        agent_run_id=run_id,
                        step_id=step.id,
                        tool_name="electricity.get_balance",
                        tool_version="1.0.0",
                        arguments={"room_id": task.structured_input["room_id"]},
                    ),
                )
            elif (
                task.target_agent == "service_agent"
                and task.structured_input.get("resolved_intent") == "electricity.create_topup_request"
                and isinstance(task.structured_input.get("room_id"), str)
                and task.structured_input.get("amount_cny") is not None
            ):
                # The top-up intent and both required slots were established
                # across prior turns; never let the model reclassify this as a
                # balance query after the final room-id reply.
                outcome = SpecialistOutcome(
                    AgentResult(
                        task_id=task.task_id,
                        agent_code="service_agent",
                        status="succeeded",
                        summary="充值参数已补充完整，正在准备提交申请。",
                        structured_output={"intent": "electricity.create_topup_request"},
                    ),
                    ToolCallRequest(
                        agent_run_id=run_id,
                        step_id=step.id,
                        tool_name="electricity.create_topup_request",
                        tool_version="1.0.0",
                        arguments={
                            "room_id": task.structured_input["room_id"],
                            "amount_cny": task.structured_input["amount_cny"],
                        },
                    ),
                )
            elif required_write_tool == "work_order.create" and _multi_work_order_reply(task):
                outcome = _honest_tool_required_outcome(task, required_write_tool)
            else:
                outcome=await specialist.invoke(task,state.user)
            if (
                self._tools is not None
                and
                required_write_tool is not None
                and outcome.result.status == "succeeded"
                and (
                    outcome.tool_request is None
                    or outcome.tool_request.tool_name != required_write_tool
                )
            ):
                corrected_task = task.model_copy(update={
                    "structured_input": {
                        **task.structured_input,
                        "requested_tool_names": [required_write_tool],
                        "tool_call_required": (
                            "这是写操作。不得声称已经完成；必须生成指定 tool_call，"
                            "最终成功答复只能由工具真实返回后生成。"
                        ),
                    },
                })
                corrected_outcome = await specialist.invoke(corrected_task, state.user)
                if (
                    corrected_outcome.result.status == "succeeded"
                    and corrected_outcome.tool_request is not None
                    and corrected_outcome.tool_request.tool_name == required_write_tool
                ):
                    outcome = corrected_outcome
                elif corrected_outcome.result.status == "needs_input":
                    outcome = corrected_outcome
                else:
                    outcome = _honest_tool_required_outcome(task, required_write_tool)
            outcome=SpecialistOutcome(await self._safety.check_output(state.user,outcome.result),outcome.tool_request)
            if outcome.tool_request is not None:
                request=outcome.tool_request.model_copy(update={"agent_run_id":run_id,"step_id":step.id})
                try:
                    prepared=self._tools.prepare(request) if self._tools else None
                except ToolArgumentInvalid:
                    # 真实模型偶发参数名漂移：给予一次带错误回显的修正机会，仍失败则按步骤失败处理
                    corrected=task.model_copy(update={"structured_input":{**task.structured_input,"tool_argument_error":"上一次 tool_call 的参数不符合 input_schema，请严格按工具的 input_schema 修正参数名与类型后重新输出 tool_call"}})
                    outcome=await specialist.invoke(corrected,state.user)
                    outcome=SpecialistOutcome(await self._safety.check_output(state.user,outcome.result),outcome.tool_request)
                    try:
                        if outcome.tool_request is not None:
                            request=outcome.tool_request.model_copy(update={"agent_run_id":run_id,"step_id":step.id})
                            prepared=self._tools.prepare(request) if self._tools else None
                        else:
                            prepared=None
                    except ToolArgumentInvalid as second_error:
                        message = _tool_argument_feedback(second_error)
                        fields = [detail.field for detail in second_error.details if detail.field]
                        outcome = SpecialistOutcome(AgentResult(
                            task_id=task.task_id,
                            agent_code=task.target_agent,
                            status="needs_input",
                            summary=message,
                            structured_output={"answer": message, "missing_slots": fields},
                        ))
                        prepared = None
            if outcome.tool_request is not None:
                if prepared is not None and request.idempotency_key is None:
                    # R2/R3 强制幂等键：LLM 不负责管理幂等语义，由运行时按
                    # run/step/tool/参数哈希确定性生成，重试与审批复用同一键。
                    request = request.model_copy(update={"idempotency_key": str(uuid5(NAMESPACE_URL, f"campuspilot:tool-call:{run_id}:{step.id}:{request.tool_name}:{prepared.arguments_hash}"))})
                call=self._trace.append_tool(run_id=run_id,step_id=step.id,tool_name=request.tool_name,tool_version=request.tool_version,arguments_hash=prepared.arguments_hash if prepared else "0"*64,arguments_summary=request.arguments,idempotency_key=request.idempotency_key)
                try:
                    await self._execute_tool(state,step.id,call.id,request,task.target_agent)
                except ToolArgumentInvalid as exc:
                    message = _tool_argument_feedback(exc)
                    await self._trace.transition_tool(
                        call.id,{"prepared","authorized","running"},"failed",
                        result_summary={},error_code=exc.code,finished_at=datetime.now(UTC),
                    )
                    await self._trace.transition_step(
                        step.id,{"running"},"partial",output_summary={"answer":message},
                        error_code=exc.code,finished_at=datetime.now(UTC),
                    )
                    state.context["_missing_slots"] = [
                        detail.field for detail in exc.details if detail.field
                    ]
                    await self._trace.transition_run(run_id,{"running"},"awaiting_input")
                    await self._checkpoints.save(run_id,state)
                    await self._events.publish(run_id,"input_required",{
                        "status":"awaiting_input","message":message,
                    })
                    return
                except ToolApprovalRequired:
                    if self._approvals is None or prepared is None:
                        raise
                    approval=await self._approvals.create(run_id=run_id,tool_call_id=call.id,user_id=state.user.user_id,action=request.tool_name,display_summary=_approval_display_summary(request.tool_name,request.arguments),arguments_hash=prepared.arguments_hash)
                    await self._trace.transition_tool(call.id,{"prepared"},"awaiting_approval")
                    await self._trace.transition_step(step.id,{"running"},"awaiting_approval")
                    await self._trace.transition_run(run_id,{"running"},"awaiting_approval")
                    state.pending_request=request; state.pending_step_id=step.id; state.pending_tool_call_id=call.id; state.pending_agent_code=task.target_agent
                    await self._checkpoints.save(run_id,state)
                    await self._events.publish(run_id,"approval_required",{"approval_id":str(approval.id),"tool_name":request.tool_name})
                    return
            else:
                step_output = dict(outcome.result.structured_output)
                if outcome.result.status == "needs_input":
                    answer = step_output.get("answer")
                    if not isinstance(answer, str) or not answer.strip():
                        step_output["answer"] = outcome.result.summary
                await self._trace.transition_step(step.id,{"running"},outcome.result.status if outcome.result.status in {"succeeded","partial","failed"} else "partial",output_summary=step_output,finished_at=datetime.now(UTC),error_code=outcome.result.error.reason if outcome.result.error else None)
                if outcome.result.status == "needs_input":
                    missing_slots = outcome.result.structured_output.get("missing_slots")
                    if not isinstance(missing_slots, list) or not all(isinstance(item, str) for item in missing_slots):
                        missing_slots = ["room_id"] if task.target_agent == "service_agent" and "电费" in state.objective else []
                    state.context["_missing_slots"] = missing_slots
                    await self._trace.transition_run(run_id, {"running"}, "awaiting_input")
                    await self._checkpoints.save(run_id, state)
                    await self._events.publish(run_id, "input_required", {
                        "status": "awaiting_input",
                        "message": outcome.result.summary,
                    })
                    return
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
        requested_tools = tuple(state.context.get("requested_tool_names") or ())
        if requested_tools:
            selected = set(requested_tools)
            allowlist = tuple(name for name in allowlist if name in selected)
        result=await self._tools.execute(context=state.user,request=request,agent_allowlist=allowlist)
        output_summary = dict(result.data or {})
        output_summary["answer"] = _tool_success_answer(request.tool_name, output_summary)
        await self._trace.transition_tool(call_id,{"prepared","awaiting_approval"},result.status,result_summary=result.data or {},duration_ms=result.duration_ms,finished_at=datetime.now(UTC),audit_id=result.audit_id)
        await self._trace.transition_step(step_id,{"running","awaiting_approval"},"succeeded",output_summary=output_summary,finished_at=datetime.now(UTC))


class InProcessRuntimeDispatcher:
    def __init__(self, runtime: BoundedGraphRuntime) -> None: self._runtime=runtime
    async def start(self, run_id, user, objective, context): await self._runtime.start(run_id,user,objective,context)
    async def resume(self, run_id, approval_id): await self._runtime.resume(run_id,approval_id)
    async def continue_input(self, run_id, user_input): await self._runtime.continue_input(run_id,user_input)
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
    async def continue_input(self, run_id, user_input): self.commands.append(RuntimeCommand("input",run_id))
    async def cancel(self, run_id): self.commands.append(RuntimeCommand("cancel",run_id))
