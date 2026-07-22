import asyncio
import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.agent_platform.models import AgentRun
from app.modules.agent_platform.run_service import AgentRunService
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision, IdempotencyReplay

NOW=datetime(2026,7,15,tzinfo=UTC); USER=uuid4(); RUN=uuid4()
class Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False
def actor(*extra): return AuthenticatedUser(USER,"student01","Student",None,None,"active",(AuthenticatedRole(uuid4(),"student","Student"),),("agent:run",*extra),None,NOW,1)
def service(decision, conversations=None):
    session=MagicMock(); session.begin.return_value=Transaction(); trace=MagicMock(); trace.create_run.return_value=AgentRun(id=RUN,user_id=USER,client_request_id="agent-request",input_summary="safe",status="created",step_count=0,specialist_count=0,created_at=NOW,updated_at=NOW); trace.finalize=AsyncMock()
    queries=MagicMock(); idem=MagicMock(); idem.begin=AsyncMock(return_value=decision); idem.complete=AsyncMock(return_value=True); dispatcher=MagicMock(); dispatcher.start=AsyncMock(); dispatcher.cancel=AsyncMock(); terminal=MagicMock(); terminal.complete=AsyncMock()
    return AgentRunService(session=session,trace=trace,queries=queries,idempotency=idem,dispatcher=dispatcher,terminal=terminal,conversations=conversations,now=lambda:NOW),dispatcher,idem

def test_create_completes_and_dispatches_with_redacted_context():
    svc,dispatcher,idem=service(IdempotencyDecision(record_id=uuid4())); result=asyncio.run(svc.create(actor=actor(),input_text="查询电费",conversation_id=None,mode="auto",context={"token":"secret","safe":"ok"},idempotency_key="key",request_id="agent-request"))
    assert result.status_code==202 and result.body["data"]["id"]==str(RUN); assert idem.begin.await_args.kwargs["request_body"]["context"]["token"]=="***"; dispatcher.start.assert_awaited_once()

def test_create_maps_explicit_mode_to_runtime_agent_selection():
    svc,dispatcher,_=service(IdempotencyDecision(record_id=uuid4()))
    asyncio.run(svc.create(actor=actor("model:read"),input_text="查询电费",conversation_id=None,mode="service",context={},idempotency_key="key",request_id="agent-request"))
    runtime_context=dispatcher.start.await_args.args[3]
    assert runtime_context["requested_agent_codes"]==["service_agent"]

def test_create_links_and_updates_owned_conversation():
    conversation_id=uuid4(); conversation=MagicMock(title="新对话")
    conversations=MagicMock(); conversations.get_owned=AsyncMock(return_value=conversation)
    svc,dispatcher,_=service(IdempotencyDecision(record_id=uuid4()),conversations)
    asyncio.run(svc.create(actor=actor("model:read"),input_text="查询我的电费余额",conversation_id=conversation_id,mode="service",context={},idempotency_key="key",request_id="agent-request"))
    conversations.get_owned.assert_awaited_once_with(conversation_id,USER,lock=True)
    assert conversation.title=="查询我的电费余额"
    assert conversation.last_message_at==NOW and conversation.updated_at==NOW
    assert dispatcher.start.await_count==1

def test_idempotency_replay_does_not_dispatch_again():
    body={"code":"OK","message":"success","data":{"id":str(RUN)},"request_id":"first-request","timestamp":NOW.isoformat()}; svc,dispatcher,_=service(IdempotencyDecision(record_id=uuid4(),replay=IdempotencyReplay(202,body,"agent_run",str(RUN))))
    result=asyncio.run(svc.create(actor=actor(),input_text="查询电费",conversation_id=None,mode="auto",context={},idempotency_key="key",request_id="new-request")); assert result.request_id=="first-request"; dispatcher.start.assert_not_awaited()

def test_list_forwards_conversation_scope():
    conversation_id=uuid4(); svc,_,_=service(IdempotencyDecision(record_id=uuid4()))
    svc._queries.list_runs=AsyncMock(return_value=MagicMock())
    asyncio.run(svc.list(actor=actor(),page=1,page_size=20,status=None,conversation_id=conversation_id))
    svc._queries.list_runs.assert_awaited_once_with(user_id=USER,can_read_all=False,page=1,page_size=20,status=None,conversation_id=conversation_id)

def test_cancel_clears_checkpoint_and_publishes_terminal_event():
    from app.modules.agent_platform.run_queries import RunAggregate
    svc,dispatcher,_=service(IdempotencyDecision(record_id=uuid4()))
    run=AgentRun(id=RUN,user_id=USER,client_request_id="agent-request",input_summary="safe",status="awaiting_approval",step_count=1,specialist_count=1,created_at=NOW,updated_at=NOW)
    svc._queries.get_aggregate=AsyncMock(return_value=RunAggregate(run,(),(),()))
    result=asyncio.run(svc.cancel(actor=actor(),run_id=RUN,idempotency_key="cancel-key",request_id="cancel-request"))
    assert result.status_code==200
    svc._terminal.complete.assert_awaited_once_with(run_id=RUN,status="cancelled",request_id="cancel-request")
    dispatcher.cancel.assert_awaited_once_with(RUN)


def test_regular_user_cannot_select_raw_agents_or_tools():
    svc,dispatcher,_=service(IdempotencyDecision(record_id=uuid4()))
    with pytest.raises(Exception) as caught:
        asyncio.run(svc.create(actor=actor(),input_text="查询电费",conversation_id=None,mode="auto",context={"requested_tool_names":["electricity.get_balance"]},idempotency_key="key",request_id="agent-request"))
    assert caught.value.code=="AGENT_DEBUG_FORBIDDEN" and caught.value.status_code==403
    dispatcher.start.assert_not_awaited()
