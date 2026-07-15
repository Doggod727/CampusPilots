from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import create_app
from app.modules.agent_platform.run_queries import RunDTO, RunDetailDTO, RunPageDTO
from app.modules.agent_platform.run_routes import get_approval_service, get_run_service
from app.modules.agent_platform.approval_decision import ApprovalMutationResult
from app.modules.agent_platform.run_service import RunMutationResult
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.user_schemas import PageMetaData
from app.shared.responses import SuccessResponse

NOW=datetime(2026,7,15,tzinfo=UTC); RUN=uuid4()
def actor(perms): return AuthenticatedUser(uuid4(),"student01","Student",None,None,"active",(AuthenticatedRole(uuid4(),"student","Student"),),tuple(perms),None,NOW,1)
def dto(status="created"): return RunDTO(id=RUN,status=status,route=None,router_model=None,router_confidence=None,input_summary="电费查询",final_answer=None,error_code=None,created_at=NOW,updated_at=NOW,finished_at=None)
def make_client(perms,service,approval_service=None):
    app=create_app()
    async def auth(): return actor(perms)
    async def get_service(): yield service
    async def get_approval(): yield approval_service or MagicMock()
    app.dependency_overrides[get_authenticated_user]=auth; app.dependency_overrides[get_run_service]=get_service
    app.dependency_overrides[get_approval_service]=get_approval
    return TestClient(app)

def test_create_returns_202_and_preserves_request_id():
    body=SuccessResponse(data=dto(),request_id="agent-request",timestamp=NOW).model_dump(mode="json"); service=MagicMock(); service.create=AsyncMock(return_value=RunMutationResult(202,"agent-request",body))
    response=make_client({"agent:run"},service).post("/api/v1/agent-runs",headers={"Idempotency-Key":"idem-1","X-Request-Id":"agent-request"},json={"input":"查询宿舍电费","context":{"token":"secret"}})
    assert response.status_code==202 and response.json()["request_id"]=="agent-request" and response.headers["X-Request-Id"]=="agent-request"
    assert service.create.await_args.kwargs["idempotency_key"]=="idem-1"

def test_list_detail_and_cancel_responses():
    service=MagicMock(); service.list=AsyncMock(return_value=RunPageDTO(items=(dto(),),pagination=PageMetaData(page=1,page_size=20,total=1,total_pages=1))); service.detail=AsyncMock(return_value=RunDetailDTO(run=dto(),steps=(),tool_calls=(),approvals=()))
    cancelled=SuccessResponse(data=dto("cancelled"),request_id="cancel-request",timestamp=NOW).model_dump(mode="json"); service.cancel=AsyncMock(return_value=RunMutationResult(200,"cancel-request",cancelled)); client=make_client({"agent:run","agent:run:read_own"},service)
    assert client.get("/api/v1/agent-runs").status_code==200
    assert client.get(f"/api/v1/agent-runs/{RUN}").json()["data"]["run"]["id"]==str(RUN)
    assert client.post(f"/api/v1/agent-runs/{RUN}/cancel",headers={"Idempotency-Key":"cancel-1"}).json()["data"]["status"]=="cancelled"

def test_permissions_headers_and_validation_are_enforced():
    service=MagicMock(); assert make_client(set(),service).get("/api/v1/agent-runs").status_code==403
    response=make_client({"agent:run"},service).post("/api/v1/agent-runs",headers={"Idempotency-Key":"x"},json={"input":"x","unknown":1})
    assert response.status_code==422 and response.json()["code"]=="VALIDATION_ERROR"

def test_health_does_not_construct_run_service(): assert make_client(set(),MagicMock()).get("/health/live").status_code==200

def test_owner_can_decide_approval_and_validation_is_strict():
    approval=MagicMock(); body={"code":"OK","message":"success","data":{"id":str(uuid4()),"run_id":str(RUN),"tool_name":"work_order.create","argument_summary":{},"argument_hash":"a"*64,"status":"approved","expires_at":NOW.isoformat(),"decided_at":NOW.isoformat(),"created_at":NOW.isoformat()},"request_id":"approval-request","timestamp":NOW.isoformat()}
    approval.decide=AsyncMock(return_value=ApprovalMutationResult(200,"approval-request",body)); client=make_client(set(),MagicMock(),approval)
    response=client.post(f"/api/v1/agent-runs/{RUN}/approvals/{uuid4()}",headers={"Idempotency-Key":"approval-1","X-Request-Id":"approval-request"},json={"decision":"approve","argument_hash":"a"*64})
    assert response.status_code==200 and response.json()["data"]["status"]=="approved"; approval.decide.assert_awaited_once()
    invalid=client.post(f"/api/v1/agent-runs/{RUN}/approvals/{uuid4()}",headers={"Idempotency-Key":"approval-2"},json={"decision":"approve","argument_hash":"short"})
    assert invalid.status_code==422
