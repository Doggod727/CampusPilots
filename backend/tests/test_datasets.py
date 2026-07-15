import asyncio,hashlib
from datetime import UTC,datetime
from unittest.mock import AsyncMock,MagicMock
from uuid import uuid4
import pytest
from app.modules.agent_platform.datasets import DatasetService,DatasetValidator,DatasetNotFound,DatasetVersionStateConflict,DuplicateDataset
from app.modules.agent_platform.models import Dataset,DatasetVersion

NOW=datetime(2026,7,15,tzinfo=UTC); DID=uuid4();USER=uuid4();DATA=b'{"text":"hello","label":"service"}\n';HASH=hashlib.sha256(DATA).hexdigest()
class Tx:
 async def __aenter__(self):return self
 async def __aexit__(self,*a):return False
def build():
 session=MagicMock();session.begin.return_value=Tx();repo=MagicMock();store=MagicMock();store.read=AsyncMock(return_value=DATA);svc=DatasetService(session,repo,store,now=lambda:NOW);return svc,repo,store
def dataset():return Dataset(id=DID,name="router",purpose="agent_router",description=None,owner_user_id=USER,created_at=NOW,updated_at=NOW)
def test_validator_recalculates_count_and_safe_report():
 count,status,report,sensitive=DatasetValidator().validate(DATA,"jsonl","agent_router",1);assert(count,status,sensitive)==(1,"valid",False) and report["errors"]==[]
 assert DatasetValidator().validate(DATA,"jsonl","agent_router",2)[1]=="invalid"
def test_create_duplicate_and_version_server_validation():
 svc,repo,store=build();repo.get_by_name=AsyncMock(return_value=None);repo.add=MagicMock();created=asyncio.run(svc.create(name="router",purpose="agent_router",description=None,actor_id=USER));assert created.name=="router"
 repo.get_by_name.return_value=dataset()
 with pytest.raises(DuplicateDataset):asyncio.run(svc.create(name="router",purpose="agent_router",description=None,actor_id=USER))
 repo.get=AsyncMock(return_value=dataset());repo.next_version=AsyncMock(return_value=1)
 version=asyncio.run(svc.create_version(dataset_id=DID,artifact_key="quarantine/a.jsonl",artifact_sha256=HASH,fmt="jsonl",claimed_count=1,split_config={},declared_sensitive=False,actor_id=USER));assert version.validation_status=="valid" and version.sample_count==1
def test_freeze_requires_valid_non_sensitive_version():
 svc,repo,_=build();repo.get=AsyncMock(return_value=dataset());item=DatasetVersion(id=uuid4(),dataset_id=DID,version=1,artifact_key="q",artifact_sha256=HASH,format="jsonl",sample_count=1,split_config={},validation_status="invalid",validation_report={},contains_sensitive_data=False,created_by=USER,created_at=NOW);repo.version=AsyncMock(return_value=item)
 with pytest.raises(DatasetVersionStateConflict):asyncio.run(svc.freeze(DID,1))
 item.validation_status="valid";result=asyncio.run(svc.freeze(DID,1));assert result.frozen_at==NOW
def test_detail_hides_missing_dataset():
 svc,repo,_=build();repo.get=AsyncMock(return_value=None)
 with pytest.raises(DatasetNotFound):asyncio.run(svc.detail(DID))
