from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from app.core.config import Settings
from app.infrastructure.database import Database
from app.modules.agent_platform.approvals import ApprovalRepository, ApprovalService
from app.modules.agent_platform.orchestration.runtime import RuntimeDispatcherPort
from app.modules.agent_platform.run_queries import AgentRunQueryRepository, AgentRunQueryService, approval_dto
from app.modules.agent_platform.tool_gateway.errors import ToolApprovalInvalid
from app.modules.agent_platform.traces import TraceRepository, TraceService
from app.modules.agent_platform.runtime_persistence import RuntimeCommandRepository
from app.modules.agent_platform.runtime_worker import OutboxRuntimeDispatcher
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.repositories import AuditLogRepository, IdempotencyRecordRepository
from app.shared.responses import SuccessResponse


@dataclass(frozen=True)
class ApprovalMutationResult:
    status_code: int
    request_id: str
    body: dict[str, Any] = field(repr=False)


class ApprovalDecisionService:
    def __init__(self, *, session, approvals:ApprovalService, queries:AgentRunQueryService,
                 trace:TraceService, idempotency:IdempotencyService,
                 audit:AuditService, dispatcher:RuntimeDispatcherPort, now=None) -> None:
        self._session=session; self._approvals=approvals; self._queries=queries; self._trace=trace
        self._idempotency=idempotency; self._audit=audit; self._dispatcher=dispatcher
        self._now=now or (lambda:datetime.now(UTC))

    async def decide(self, *, actor:AuthenticatedUser, run_id:UUID, approval_id:UUID,
                     decision:Literal["approve","reject"], argument_hash:str,
                     comment:str|None, idempotency_key:str, request_id:str) -> ApprovalMutationResult:
        should_resume=False
        async with self._session.begin():
            idem=await self._idempotency.begin(user_id=actor.user_id,endpoint=f"POST /api/v1/agent-runs/{run_id}/approvals/{approval_id}",idempotency_key=idempotency_key,request_body={"decision":decision,"argument_hash":argument_hash,"comment":comment})
            if idem.replay: return ApprovalMutationResult(idem.replay.response_status,str(idem.replay.response_body["request_id"]),dict(idem.replay.response_body))
            if idem.pending: raise IdempotencyConflict()
            aggregate=await self._queries.get_aggregate(run_id=run_id,user_id=actor.user_id,can_read_all=False)
            if aggregate is None: raise ToolApprovalInvalid()
            approval=await self._approvals.decide(approval_id=approval_id,run_id=run_id,user_id=actor.user_id,decision=decision,arguments_hash=argument_hash)
            calls={item.id:item for item in aggregate.tool_calls}; call=calls.get(approval.tool_call_id)
            if call is None: raise ToolApprovalInvalid()
            if decision=="reject":
                await self._trace.transition_tool(call.id,{"awaiting_approval"},"rejected",error_code="TOOL_APPROVAL_REJECTED",finished_at=self._utc())
                await self._trace.transition_step(call.step_id,{"awaiting_approval"},"failed",error_code="TOOL_APPROVAL_REJECTED",finished_at=self._utc())
                await self._trace.finalize(run_id,"partial",finish_reason="approval_rejected",error_code="TOOL_APPROVAL_REJECTED")
            else:
                await self._dispatcher.resume(run_id,approval_id)
                should_resume=True
            data=approval_dto(approval,call)
            response=SuccessResponse(data=data,request_id=request_id,timestamp=self._utc()).model_dump(mode="json")
            self._audit.record_success(action="agent.approval.decide",resource_type="agent_approval",resource_id=str(approval.id),request_id=request_id,actor_user_id=actor.user_id,actor_username=actor.username,after_data={"run_id":str(run_id),"decision":decision,"comment_provided":comment is not None})
            if not await self._idempotency.complete(record_id=idem.record_id,response_status=200,response_body=response,resource_type="agent_approval",resource_id=str(approval.id)):
                raise ToolApprovalInvalid()
        if should_resume and isinstance(self._dispatcher,OutboxRuntimeDispatcher):
            await self._dispatcher.notify_best_effort()
        return ApprovalMutationResult(200,request_id,response)

    def _utc(self):
        value=self._now(); return value if value.tzinfo else value.replace(tzinfo=UTC)


@asynccontextmanager
async def approval_decision_service_context(settings:Settings):
    database=Database.from_settings(settings)
    try:
        async with database.session() as session:
            dispatcher=OutboxRuntimeDispatcher(RuntimeCommandRepository(session),max_attempts=settings.agent_runtime_max_attempts)
            yield ApprovalDecisionService(session=session,approvals=ApprovalService(ApprovalRepository(session),ttl_seconds=settings.approval_ttl_seconds),queries=AgentRunQueryService(AgentRunQueryRepository(session)),trace=TraceService(TraceRepository(session)),idempotency=IdempotencyService(session=session,repository=IdempotencyRecordRepository(session)),audit=AuditService(AuditLogRepository(session)),dispatcher=dispatcher)
    finally: await database.dispose()
