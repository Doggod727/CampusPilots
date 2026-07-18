from __future__ import annotations
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC,datetime
from typing import Annotated,Any,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,File,Header,Query,Request,UploadFile
from pydantic import BaseModel,ConfigDict,Field
from starlette.responses import JSONResponse
from app.core.config import Settings,get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.agent_platform.artifact_store import DatasetArtifactStore,StoredDatasetArtifact
from app.modules.agent_platform.datasets import DatasetDTO,DatasetDetailDTO,DatasetPageDTO,DatasetRepository,DatasetService,DatasetVersionDTO
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyConflict,IdempotencyService
from app.modules.platform.repositories import AuditLogRepository,IdempotencyRecordRepository
from app.shared.responses import SuccessResponse

router=APIRouter(prefix="/api/v1/datasets",tags=["Datasets"])
class DatasetCreateRequest(BaseModel):
 model_config=ConfigDict(extra="forbid");name:str=Field(min_length=2,max_length=100);purpose:Literal["agent_router","instruction_tuning","rag_reranker","evaluation"];description:str|None=Field(default=None,max_length=500)
class VersionCreateRequest(BaseModel):
 model_config=ConfigDict(extra="forbid");artifact_key:str=Field(min_length=3,max_length=500);artifact_sha256:str=Field(pattern=r"^[0-9a-f]{64}$");format:Literal["jsonl","csv"];sample_count:int=Field(ge=1);split_config:dict[str,Any];contains_sensitive_data:bool
class UploadDTO(BaseModel):
 model_config=ConfigDict(extra="forbid",frozen=True);artifact_key:str;artifact_sha256:str;file_name:str;format:str;size_bytes:int;expires_at:datetime
DatasetResponse=SuccessResponse[DatasetDTO];DatasetDetailResponse=SuccessResponse[DatasetDetailDTO];DatasetListResponse=SuccessResponse[DatasetPageDTO];VersionResponse=SuccessResponse[DatasetVersionDTO];UploadResponse=SuccessResponse[UploadDTO]

class DatasetApiService:
 def __init__(self,session,core,store,idem,audit,now=None):self.session=session;self.core=core;self.store=store;self.idem=idem;self.audit=audit;self.now=now or(lambda:datetime.now(UTC))
 async def mutation(self,*,actor,key,request_id,endpoint,request_body,status,resource_type,operation,audit_action=None):
  async with self.session.begin():
   d=await self.idem.begin(user_id=actor.user_id,endpoint=endpoint,idempotency_key=key,request_body=request_body)
   if d.replay:return d.replay.response_status,dict(d.replay.response_body),str(d.replay.response_body["request_id"])
   if d.pending:raise IdempotencyConflict()
   data=await operation()
   body=SuccessResponse(data=data,request_id=request_id,timestamp=self.now()).model_dump(mode="json")
   rid=str(getattr(data,"id",request_body.get("dataset_id",""))) or None
   if audit_action:self.audit.record_success(action=audit_action,resource_type=resource_type,resource_id=rid,request_id=request_id,actor_user_id=actor.user_id,actor_username=actor.username,after_data=data.model_dump(mode="json") if hasattr(data,"model_dump") else {"completed":True})
   if not await self.idem.complete(record_id=d.record_id,response_status=status,response_body=body,resource_type=resource_type,resource_id=rid):raise IdempotencyConflict()
  return status,body,request_id
 async def upload(self,actor,key,request_id,dataset_id,file):
  artifact=await self.store.store(file);dto=UploadDTO(**artifact.__dict__)
  async def operation():
   # 数据集存在性检查必须在 mutation 事务内执行：事务外裸读会占用会话事务，
   # 导致 mutation 的 session.begin() 报"A transaction is already begun"（真实环境发现的缺陷）。
   await self.core.detail(dataset_id)
   return dto
  try:
   result=await self.mutation(actor=actor,key=key,request_id=request_id,endpoint=f"POST /api/v1/datasets/{dataset_id}/uploads",request_body={"dataset_id":str(dataset_id),"sha256":artifact.artifact_sha256,"format":artifact.format},status=201,resource_type="dataset_upload",operation=operation)
   if result[1].get("data",{}).get("artifact_key")!=artifact.artifact_key:await self.store.delete(artifact.artifact_key)
   return result
  except BaseException:
   await self.store.delete(artifact.artifact_key);raise

async def _value(value):return value
@asynccontextmanager
async def context(settings:Settings):
 db=Database.from_settings(settings)
 try:
  async with db.session() as session:
   store=DatasetArtifactStore(settings.dataset_artifact_root,ttl_seconds=settings.dataset_upload_ttl_seconds)
   core=DatasetService(session,DatasetRepository(session),store,manage_transaction=False)
   yield DatasetApiService(session,core,store,IdempotencyService(session=session,repository=IdempotencyRecordRepository(session)),AuditService(AuditLogRepository(session)))
 finally:await db.dispose()
async def get_service()->AsyncIterator[DatasetApiService]:
 async with context(get_settings()) as service:yield service

def _json(result):status,body,rid=result;return JSONResponse(body,status_code=status,headers={REQUEST_ID_HEADER:rid})
@router.get("",operation_id="listDatasets",response_model=DatasetListResponse)
async def list_(request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("dataset:read"))],service:Annotated[DatasetApiService,Depends(get_service)],page:Annotated[int,Query(ge=1)]=1,page_size:Annotated[int,Query(ge=1,le=100)]=20):return SuccessResponse(data=await service.core.list(page,page_size),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("",operation_id="createDataset",status_code=201,response_model=DatasetResponse)
async def create(payload:DatasetCreateRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("dataset:write"))],service:Annotated[DatasetApiService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return _json(await service.mutation(actor=actor,key=key,request_id=request.state.request_id,endpoint="POST /api/v1/datasets",request_body=payload.model_dump(mode="json"),status=201,resource_type="dataset",audit_action="dataset.create",operation=lambda:service.core.create(name=payload.name,purpose=payload.purpose,description=payload.description,actor_id=actor.user_id)))
@router.get("/{dataset_id}",operation_id="getDataset",response_model=DatasetDetailResponse)
async def detail(dataset_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("dataset:read"))],service:Annotated[DatasetApiService,Depends(get_service)]):return SuccessResponse(data=await service.core.detail(dataset_id),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.delete("/{dataset_id}",operation_id="deleteDataset")
async def delete(dataset_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("dataset:write"))],service:Annotated[DatasetApiService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return _json(await service.mutation(actor=actor,key=key,request_id=request.state.request_id,endpoint=f"DELETE /api/v1/datasets/{dataset_id}",request_body={"dataset_id":str(dataset_id)},status=200,resource_type="dataset",audit_action="dataset.delete",operation=lambda:_delete(service.core,dataset_id)))
async def _delete(core,did):await core.delete(did);return {}
@router.post("/{dataset_id}/uploads",operation_id="uploadDatasetArtifact",status_code=201,response_model=UploadResponse)
async def upload(dataset_id:UUID,request:Request,file:Annotated[UploadFile,File()],actor:Annotated[AuthenticatedUser,Depends(require_permissions("dataset:write"))],service:Annotated[DatasetApiService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return _json(await service.upload(actor,key,request.state.request_id,dataset_id,file))
@router.post("/{dataset_id}/versions",operation_id="createDatasetVersion",status_code=201,response_model=VersionResponse)
async def version(dataset_id:UUID,payload:VersionCreateRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("dataset:write"))],service:Annotated[DatasetApiService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return _json(await service.mutation(actor=actor,key=key,request_id=request.state.request_id,endpoint=f"POST /api/v1/datasets/{dataset_id}/versions",request_body={"dataset_id":str(dataset_id),**payload.model_dump(mode="json")},status=201,resource_type="dataset_version",operation=lambda:service.core.create_version(dataset_id=dataset_id,artifact_key=payload.artifact_key,artifact_sha256=payload.artifact_sha256,fmt=payload.format,claimed_count=payload.sample_count,split_config=payload.split_config,declared_sensitive=payload.contains_sensitive_data,actor_id=actor.user_id)))
@router.post("/{dataset_id}/versions/{version}/freeze",operation_id="freezeDatasetVersion",response_model=VersionResponse)
async def freeze(dataset_id:UUID,version:int,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("dataset:write"))],service:Annotated[DatasetApiService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return _json(await service.mutation(actor=actor,key=key,request_id=request.state.request_id,endpoint=f"POST /api/v1/datasets/{dataset_id}/versions/{version}/freeze",request_body={"dataset_id":str(dataset_id),"version":version},status=200,resource_type="dataset_version",audit_action="dataset.freeze",operation=lambda:service.core.freeze(dataset_id,version)))
