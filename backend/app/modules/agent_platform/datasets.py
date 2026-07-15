from __future__ import annotations

import csv, io, json, re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.agent_platform.artifact_store import DatasetArtifactStore
from app.modules.agent_platform.models import Dataset, DatasetVersion, TrainingJob
from app.modules.platform.user_schemas import PageMetaData

class DatasetNotFound(AppError):
    def __init__(self): super().__init__(status_code=404,code="DATASET_NOT_FOUND",message="数据集不存在")
class DatasetInUse(AppError):
    def __init__(self): super().__init__(status_code=409,code="DATASET_IN_USE",message="数据集正在被训练任务使用")
class DatasetVersionNotFound(AppError):
    def __init__(self): super().__init__(status_code=404,code="DATASET_VERSION_NOT_FOUND",message="数据集版本不存在")
class DatasetVersionStateConflict(AppError):
    def __init__(self): super().__init__(status_code=409,code="DATASET_VERSION_STATE_CONFLICT",message="数据集版本当前状态不允许该操作")
class DuplicateDataset(AppError):
    def __init__(self): super().__init__(status_code=409,code="DUPLICATE_RESOURCE",message="数据集名称已存在")

class DatasetDTO(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    id:UUID; name:str; purpose:str; description:str|None; latest_version:int|None; created_at:datetime; updated_at:datetime
class DatasetVersionDTO(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    version:int; artifact_sha256:str; format:str; sample_count:int; split_config:dict[str,Any]; validation_status:str; validation_report:dict[str,Any]; contains_sensitive_data:bool; frozen_at:datetime|None; created_at:datetime
class DatasetDetailDTO(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    dataset:DatasetDTO; versions:tuple[DatasetVersionDTO,...]
class DatasetPageDTO(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    items:tuple[DatasetDTO,...]; pagination:PageMetaData

class DatasetRepository:
    ACTIVE_TRAINING={"queued","preparing","training","evaluating"}
    def __init__(self,session:AsyncSession): self._session=session
    async def list(self,page:int,page_size:int):
        filters=(Dataset.deleted_at.is_(None),)
        total=(await self._session.execute(select(func.count()).select_from(Dataset).where(*filters))).scalar_one()
        rows=tuple((await self._session.execute(select(Dataset).where(*filters).order_by(Dataset.created_at.desc(),Dataset.id.desc()).offset((page-1)*page_size).limit(page_size))).scalars().all())
        versions=await self._latest_versions([x.id for x in rows]); return rows,versions,total
    async def get(self,dataset_id:UUID,*,lock=False):
        stmt=select(Dataset).where(Dataset.id==dataset_id,Dataset.deleted_at.is_(None))
        if lock:stmt=stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()
    async def get_by_name(self,name:str):
        return (await self._session.execute(select(Dataset).where(func.lower(Dataset.name)==name.lower(),Dataset.deleted_at.is_(None)))).scalar_one_or_none()
    def add(self,item): self._session.add(item)
    async def versions(self,dataset_id:UUID):
        return tuple((await self._session.execute(select(DatasetVersion).where(DatasetVersion.dataset_id==dataset_id).order_by(DatasetVersion.version.desc()))).scalars().all())
    async def version(self,dataset_id:UUID,version:int):
        return (await self._session.execute(select(DatasetVersion).where(DatasetVersion.dataset_id==dataset_id,DatasetVersion.version==version))).scalar_one_or_none()
    async def next_version(self,dataset_id:UUID):
        value=(await self._session.execute(select(func.coalesce(func.max(DatasetVersion.version),0)).where(DatasetVersion.dataset_id==dataset_id))).scalar_one(); return value+1
    async def in_use(self,dataset_id:UUID):
        stmt=select(func.count()).select_from(TrainingJob).join(DatasetVersion,TrainingJob.dataset_version_id==DatasetVersion.id).where(DatasetVersion.dataset_id==dataset_id,TrainingJob.status.in_(self.ACTIVE_TRAINING))
        return (await self._session.execute(stmt)).scalar_one()>0
    async def _latest_versions(self,ids):
        if not ids:return {}
        rows=(await self._session.execute(select(DatasetVersion.dataset_id,func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id.in_(ids)).group_by(DatasetVersion.dataset_id))).all(); return dict(rows)

class DatasetValidator:
    LABELS={"knowledge","service","community","governance","modelops","clarify"}
    SENSITIVE=re.compile(r"password|token|authorization|cookie|api[_-]?key|secret",re.I)
    def validate(self,data:bytes,fmt:str,purpose:str,claimed_count:int)->tuple[int,str,dict,bool]:
        errors=[]; sensitive=False
        try:
            if fmt=="jsonl":
                rows=[json.loads(line) for line in data.decode("utf-8-sig").splitlines() if line.strip()]
                for index,row in enumerate(rows,1):
                    if not isinstance(row,dict) or not self._valid(row,purpose): errors.append({"row":index,"reason":"schema_invalid"})
                    sensitive=sensitive or (isinstance(row,dict) and any(self.SENSITIVE.search(str(k)) for k in row))
            elif fmt=="csv":
                rows=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
                required={"text","label"} if purpose=="agent_router" else ({"query","positive"} if purpose=="rag_reranker" else set())
                if required and (not rows or not required.issubset(rows[0])): errors.append({"row":0,"reason":"columns_invalid"})
                sensitive=any(any(self.SENSITIVE.search(str(k)) for k in row) for row in rows)
            else: rows=[]; errors.append({"row":0,"reason":"format_invalid"})
        except (UnicodeDecodeError,json.JSONDecodeError,csv.Error): rows=[]; errors=[{"row":0,"reason":"parse_failed"}]
        count=len(rows)
        if count!=claimed_count: errors.append({"row":0,"reason":"sample_count_mismatch"})
        return count,"invalid" if errors else "valid",{"error_count":len(errors),"errors":errors[:20]},sensitive
    def _valid(self,row,purpose):
        if purpose=="agent_router":return isinstance(row.get("text"),str) and row.get("label") in self.LABELS
        if purpose=="instruction_tuning":return isinstance(row.get("messages"),list) and isinstance(row.get("metadata",{}),dict)
        if purpose=="rag_reranker":return isinstance(row.get("query"),str) and isinstance(row.get("positive"),str) and isinstance(row.get("negatives",[]),list)
        return bool(row)

class DatasetService:
    def __init__(self,session:AsyncSession,repository:DatasetRepository,store:DatasetArtifactStore,validator:DatasetValidator|None=None,now=None,manage_transaction:bool=True):
        self._session=session;self._repo=repository;self._store=store;self._validator=validator or DatasetValidator();self._now=now or(lambda:datetime.now(UTC));self._manage_transaction=manage_transaction
    async def list(self,page,page_size):
        rows,versions,total=await self._repo.list(page,page_size);return DatasetPageDTO(items=tuple(_dataset(x,versions.get(x.id)) for x in rows),pagination=PageMetaData(page=page,page_size=page_size,total=total,total_pages=ceil(total/page_size) if total else 0))
    async def detail(self,dataset_id):
        item=await self._repo.get(dataset_id)
        if item is None:raise DatasetNotFound()
        versions=await self._repo.versions(dataset_id);return DatasetDetailDTO(dataset=_dataset(item,versions[0].version if versions else None),versions=tuple(_version(v) for v in versions))
    async def create(self,*,name,purpose,description,actor_id):
        async with self._transaction():
            if await self._repo.get_by_name(name):raise DuplicateDataset()
            now=self._utc();item=Dataset(id=uuid4(),name=name,purpose=purpose,description=description,owner_user_id=actor_id,created_at=now,updated_at=now);self._repo.add(item)
        return _dataset(item,None)
    async def create_version(self,*,dataset_id,artifact_key,artifact_sha256,fmt,claimed_count,split_config,declared_sensitive,actor_id):
        async with self._transaction():
            dataset=await self._repo.get(dataset_id,lock=True)
            if dataset is None:raise DatasetNotFound()
            data=await self._store.read(artifact_key,expected_sha256=artifact_sha256)
            if not artifact_key.endswith("."+fmt):raise DatasetVersionStateConflict()
            count,status,report,detected=self._validator.validate(data,fmt,dataset.purpose,claimed_count)
            now=self._utc();item=DatasetVersion(id=uuid4(),dataset_id=dataset_id,version=await self._repo.next_version(dataset_id),artifact_key=artifact_key,artifact_sha256=artifact_sha256,format=fmt,sample_count=count,split_config=dict(split_config),validation_status=status,validation_report=report,contains_sensitive_data=declared_sensitive or detected,created_by=actor_id,created_at=now);self._repo.add(item)
        return _version(item)
    async def freeze(self,dataset_id,version):
        async with self._transaction():
            dataset=await self._repo.get(dataset_id,lock=True)
            if dataset is None:raise DatasetNotFound()
            item=await self._repo.version(dataset_id,version)
            if item is None:raise DatasetVersionNotFound()
            if item.frozen_at is not None:return _version(item)
            if item.validation_status!="valid" or item.contains_sensitive_data:raise DatasetVersionStateConflict()
            item.frozen_at=self._utc()
        return _version(item)
    async def delete(self,dataset_id):
        async with self._transaction():
            item=await self._repo.get(dataset_id,lock=True)
            if item is None:raise DatasetNotFound()
            if await self._repo.in_use(dataset_id):raise DatasetInUse()
            item.deleted_at=self._utc();item.updated_at=self._utc()
    def _utc(self):
        v=self._now();return v if v.tzinfo else v.replace(tzinfo=UTC)
    @asynccontextmanager
    async def _transaction(self):
        if self._manage_transaction:
            async with self._session.begin(): yield
        else: yield

def _dataset(x,latest):return DatasetDTO(id=x.id,name=x.name,purpose=x.purpose,description=x.description,latest_version=latest,created_at=x.created_at,updated_at=x.updated_at)
def _version(x):return DatasetVersionDTO(version=x.version,artifact_sha256=x.artifact_sha256,format=x.format,sample_count=x.sample_count,split_config=x.split_config,validation_status=x.validation_status,validation_report=x.validation_report,contains_sensitive_data=x.contains_sensitive_data,frozen_at=x.frozen_at,created_at=x.created_at)
