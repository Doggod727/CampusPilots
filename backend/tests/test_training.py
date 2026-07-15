import asyncio
from datetime import UTC,datetime
from unittest.mock import AsyncMock,MagicMock
from uuid import uuid4
import pytest
from sqlalchemy.dialects import postgresql
from app.modules.agent_platform.models import DatasetVersion,TrainingJob
from app.modules.agent_platform.training import TrainingBaseModelNotAllowed,TrainingCreateRequest,TrainingDatasetNotReady,TrainingRepository,TrainingService
from app.modules.platform.auth import AuthenticatedRole,AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision
NOW=datetime(2026,7,15,tzinfo=UTC);USER=uuid4();DID=uuid4()
class Tx:
 async def __aenter__(self):return self
 async def __aexit__(self,*a):return False
def actor():return AuthenticatedUser(USER,"model01","Model",None,None,"active",(AuthenticatedRole(uuid4(),"model_engineer","Model"),),(),None,NOW,1)
def payload(base="Qwen/Qwen2.5-1.5B-Instruct"):return TrainingCreateRequest(dataset_id=DID,dataset_version=1,base_model=base,method="lora",config={"epochs":1,"learning_rate":.001,"batch_size":2})
def service(ready=True):
 s=MagicMock();s.begin.return_value=Tx();r=MagicMock();r.ready_version=AsyncMock(return_value=DatasetVersion(id=uuid4(),dataset_id=DID,version=1,artifact_key="x",artifact_sha256="a"*64,format="jsonl",sample_count=1,split_config={},validation_status="valid",validation_report={},contains_sensitive_data=False,frozen_at=NOW,created_by=USER,created_at=NOW)if ready else None);r.add=MagicMock();idem=MagicMock();idem.begin=AsyncMock(return_value=IdempotencyDecision(record_id=uuid4()));idem.complete=AsyncMock(return_value=True);return TrainingService(s,r,idem,MagicMock(),{"Qwen/Qwen2.5-1.5B-Instruct"},now=lambda:NOW),r
def test_create_queues_only_ready_allowed_training():
 svc,repo=service();status,body,_=asyncio.run(svc.create(actor(),payload(),"key","training-request"));assert status==202 and body["data"]["status"]=="queued" and body["data"]["progress"]==0;assert repo.add.call_args.args[0].artifact_key is None
def test_rejects_unapproved_base_and_unready_dataset():
 svc,_=service()
 with pytest.raises(TrainingBaseModelNotAllowed):asyncio.run(svc.create(actor(),payload("deepseek-v4-pro"),"key","request-1"))
 svc,_=service(False)
 with pytest.raises(TrainingDatasetNotReady):asyncio.run(svc.create(actor(),payload(),"key","request-1"))
def test_claim_uses_skip_locked():
 s=MagicMock();statements=[]
 async def execute(stmt):statements.append(stmt);v=MagicMock();v.scalars.return_value.all.return_value=[];return v
 s.execute=AsyncMock(side_effect=execute);asyncio.run(TrainingRepository(s).claim(2));assert "SKIP LOCKED" in str(statements[0].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
