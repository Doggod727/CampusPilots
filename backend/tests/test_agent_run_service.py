import asyncio
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
def actor(): return AuthenticatedUser(USER,"student01","Student",None,None,"active",(AuthenticatedRole(uuid4(),"student","Student"),),("agent:run",),None,NOW,1)
def service(decision):
    session=MagicMock(); session.begin.return_value=Transaction(); trace=MagicMock(); trace.create_run.return_value=AgentRun(id=RUN,user_id=USER,client_request_id="agent-request",input_summary="safe",status="created",step_count=0,specialist_count=0,created_at=NOW,updated_at=NOW); trace.finalize=AsyncMock()
    queries=MagicMock(); idem=MagicMock(); idem.begin=AsyncMock(return_value=decision); idem.complete=AsyncMock(return_value=True); dispatcher=MagicMock(); dispatcher.start=AsyncMock(); dispatcher.cancel=AsyncMock()
    return AgentRunService(session=session,trace=trace,queries=queries,idempotency=idem,dispatcher=dispatcher,now=lambda:NOW),dispatcher,idem

def test_create_completes_and_dispatches_with_redacted_context():
    svc,dispatcher,idem=service(IdempotencyDecision(record_id=uuid4())); result=asyncio.run(svc.create(actor=actor(),input_text="查询电费",conversation_id=None,mode="auto",context={"token":"secret","safe":"ok"},idempotency_key="key",request_id="agent-request"))
    assert result.status_code==202 and result.body["data"]["id"]==str(RUN); assert idem.begin.await_args.kwargs["request_body"]["context"]["token"]=="***"; dispatcher.start.assert_awaited_once()

def test_idempotency_replay_does_not_dispatch_again():
    body={"code":"OK","message":"success","data":{"id":str(RUN)},"request_id":"first-request","timestamp":NOW.isoformat()}; svc,dispatcher,_=service(IdempotencyDecision(record_id=uuid4(),replay=IdempotencyReplay(202,body,"agent_run",str(RUN))))
    result=asyncio.run(svc.create(actor=actor(),input_text="查询电费",conversation_id=None,mode="auto",context={},idempotency_key="key",request_id="new-request")); assert result.request_id=="first-request"; dispatcher.start.assert_not_awaited()
