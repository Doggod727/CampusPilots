import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.agent_platform.approval_decision import ApprovalDecisionService
from app.modules.agent_platform.models import AgentRun, ApprovalRequestModel, ToolCall
from app.modules.agent_platform.run_queries import RunAggregate
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision

NOW=datetime(2026,7,15,tzinfo=UTC); USER=uuid4(); RUN=uuid4(); CALL=uuid4(); STEP=uuid4(); APPROVAL=uuid4(); HASH="a"*64
class Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False
def actor(): return AuthenticatedUser(USER,"student01","Student",None,None,"active",(AuthenticatedRole(uuid4(),"student","Student"),),(),None,NOW,1)
def build(decision="approve"):
    session=MagicMock(); session.begin.return_value=Transaction(); approval=ApprovalRequestModel(id=APPROVAL,run_id=RUN,tool_call_id=CALL,user_id=USER,action="work_order.create",display_summary="safe",arguments_hash=HASH,status="approved" if decision=="approve" else "rejected",expires_at=NOW+timedelta(minutes=5),created_at=NOW,decided_at=NOW,decided_by=USER)
    approvals=MagicMock(); approvals.decide=AsyncMock(return_value=approval); call=ToolCall(id=CALL,run_id=RUN,step_id=STEP,tool_name="work_order.create",tool_version="1.0.0",arguments_hash=HASH,arguments_summary={},result_summary={},status="awaiting_approval",created_at=NOW); run=AgentRun(id=RUN,user_id=USER,client_request_id="req",input_summary="safe",status="awaiting_approval",step_count=1,specialist_count=1,created_at=NOW,updated_at=NOW)
    queries=MagicMock(); queries.get_aggregate=AsyncMock(return_value=RunAggregate(run,(),(call,),(approval,))); trace=MagicMock(); trace.transition_tool=AsyncMock(); trace.transition_step=AsyncMock(); trace.finalize=AsyncMock(); idem=MagicMock(); idem.begin=AsyncMock(return_value=IdempotencyDecision(record_id=uuid4())); idem.complete=AsyncMock(return_value=True); audit=MagicMock(); dispatcher=MagicMock(); dispatcher.resume=AsyncMock(); terminal=MagicMock(); terminal.complete=AsyncMock()
    return ApprovalDecisionService(session=session,approvals=approvals,queries=queries,trace=trace,idempotency=idem,audit=audit,dispatcher=dispatcher,terminal=terminal,now=lambda:NOW),trace,dispatcher,audit

def test_approve_records_safe_audit_and_resumes_after_decision():
    service,trace,dispatcher,audit=build("approve"); result=asyncio.run(service.decide(actor=actor(),run_id=RUN,approval_id=APPROVAL,decision="approve",argument_hash=HASH,comment="private",idempotency_key="key",request_id="approval-request"))
    assert result.body["data"]["status"]=="approved"; dispatcher.resume.assert_awaited_once_with(RUN,APPROVAL); trace.finalize.assert_not_awaited(); assert audit.record_success.call_args.kwargs["after_data"]["comment_provided"] is True and "private" not in str(audit.record_success.call_args)

def test_reject_terminates_without_resume():
    service,trace,dispatcher,_=build("reject"); result=asyncio.run(service.decide(actor=actor(),run_id=RUN,approval_id=APPROVAL,decision="reject",argument_hash=HASH,comment=None,idempotency_key="key",request_id="approval-request"))
    assert result.body["data"]["status"]=="rejected"; trace.transition_tool.assert_awaited_once(); trace.transition_step.assert_awaited_once(); trace.finalize.assert_awaited_once_with(RUN,"partial",finish_reason="approval_rejected",error_code="TOOL_APPROVAL_REJECTED"); dispatcher.resume.assert_not_awaited()
    service._terminal.complete.assert_awaited_once_with(run_id=RUN,status="partial",request_id="approval-request",error_code="TOOL_APPROVAL_REJECTED")
