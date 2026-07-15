import asyncio
from datetime import UTC,datetime
from unittest.mock import AsyncMock,MagicMock
from uuid import uuid4
import pytest
from app.modules.agent_platform.model_registry import ModelEvaluationRequired,ModelFallbackRequired,ModelRegisterRequest,ModelService,dto
from app.modules.agent_platform.models import ModelVersion
from app.modules.platform.auth import AuthenticatedRole,AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision
NOW=datetime(2026,7,15,tzinfo=UTC);USER=uuid4();MID=uuid4()
class Tx:
 async def __aenter__(self):return self
 async def __aexit__(self,*a):return False
def actor():return AuthenticatedUser(USER,"model01","Model",None,None,"active",(AuthenticatedRole(uuid4(),"model_engineer","Model"),),(),None,NOW,1)
def model(provider="local",purpose="agent_router",status="candidate"):return ModelVersion(id=MID,name="router",purpose=purpose,provider=provider,base_model="Qwen",version="1",quantization=None,artifact_key="models/a",artifact_sha256="a"*64,config={"api_key":"raw","safe":1},metrics={},status=status,created_by=USER,created_at=NOW,activated_at=NOW if status=="active" else None)
def service(item=None,evaluated=True):
 s=MagicMock();s.begin.return_value=Tx();r=MagicMock();r.get=AsyncMock(return_value=item);r.evaluated=AsyncMock(return_value=evaluated);r.deactivate_purpose=AsyncMock();r.duplicate=AsyncMock(return_value=False);r.add=MagicMock();idem=MagicMock();idem.begin=AsyncMock(return_value=IdempotencyDecision(record_id=uuid4()));idem.complete=AsyncMock(return_value=True);return ModelService(s,r,idem,MagicMock(),MagicMock(),now=lambda:NOW),r
def test_public_dto_removes_secret_config_fields():assert dto(model()).config=={"safe":1} and "raw"not in repr(dto(model()))
def test_register_deepseek_keeps_only_environment_reference_in_storage_and_not_response():
 svc,repo=service();p=ModelRegisterRequest(name="deepseek",purpose="complex_generation",provider="deepseek",base_model="deepseek-v4-pro",version="api",config={"api_key_env":"DEEPSEEK_API_KEY"});status,body,_=asyncio.run(svc.register(actor(),p,"key","model-request"));assert status==201 and body["data"]["status"]=="candidate" and "api_key"not in str(body["data"]).lower();assert repo.add.called
def test_activation_requires_evaluation_and_preserves_complex_fallback():
 svc,_=service(model(),False)
 with pytest.raises(ModelEvaluationRequired):asyncio.run(svc.change(actor(),MID,"activate","key","request-1"))
 svc,_=service(model(provider="local",purpose="complex_generation"),True)
 with pytest.raises(ModelFallbackRequired):asyncio.run(svc.change(actor(),MID,"activate","key","request-1"))
 svc,repo=service(model(provider="local",purpose="agent_router"),True);status,body,_=asyncio.run(svc.change(actor(),MID,"activate","key","request-1"));assert status==200 and body["data"]["status"]=="active";repo.deactivate_purpose.assert_awaited_once()
