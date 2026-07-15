from datetime import UTC,datetime
from unittest.mock import AsyncMock,MagicMock
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import create_app
from app.modules.agent_platform.dataset_routes import get_service
from app.modules.agent_platform.datasets import DatasetDTO,DatasetPageDTO
from app.modules.platform.auth import AuthenticatedRole,AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.user_schemas import PageMetaData
from app.shared.responses import SuccessResponse
NOW=datetime(2026,7,15,tzinfo=UTC);USER=uuid4();DID=uuid4()
def actor(perms):return AuthenticatedUser(USER,"model01","Model",None,None,"active",(AuthenticatedRole(uuid4(),"model_engineer","Model"),),tuple(perms),None,NOW,1)
def dto():return DatasetDTO(id=DID,name="router",purpose="agent_router",description=None,latest_version=None,created_at=NOW,updated_at=NOW)
def client(service,perms):
 app=create_app()
 async def auth():return actor(perms)
 async def svc():yield service
 app.dependency_overrides[get_authenticated_user]=auth;app.dependency_overrides[get_service]=svc;return TestClient(app)
def test_list_and_create_contract_permissions():
 service=MagicMock();service.core=MagicMock();service.core.list=AsyncMock(return_value=DatasetPageDTO(items=(dto(),),pagination=PageMetaData(page=1,page_size=20,total=1,total_pages=1)))
 body=SuccessResponse(data=dto(),request_id="dataset-request",timestamp=NOW).model_dump(mode="json");service.mutation=AsyncMock(return_value=(201,body,"dataset-request"))
 c=client(service,{"dataset:read","dataset:write"});assert c.get("/api/v1/datasets").status_code==200
 r=c.post("/api/v1/datasets",headers={"Idempotency-Key":"x","X-Request-Id":"dataset-request"},json={"name":"router","purpose":"agent_router"});assert r.status_code==201 and r.json()["data"]["id"]==str(DID)
 assert client(service,set()).get("/api/v1/datasets").status_code==403
def test_upload_requires_supported_extension_and_idempotency_header():
 service=MagicMock();service.core=MagicMock();service.core.detail=AsyncMock();service.upload=AsyncMock(return_value=(201,{"code":"OK","message":"success","data":{"artifact_key":"quarantine/a.jsonl","artifact_sha256":"a"*64,"file_name":"x.jsonl","format":"jsonl","size_bytes":3,"expires_at":NOW.isoformat()},"request_id":"upload-request","timestamp":NOW.isoformat()},"upload-request"))
 c=client(service,{"dataset:write"});assert c.post(f"/api/v1/datasets/{DID}/uploads",files={"file":("x.jsonl",b"{}\n")}).status_code==422
 r=c.post(f"/api/v1/datasets/{DID}/uploads",headers={"Idempotency-Key":"up"},files={"file":("x.jsonl",b"{}\n")});assert r.status_code==201
