from __future__ import annotations
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC,datetime
from math import ceil
from typing import Annotated,Any,Literal
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,Header,Query,Request
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import func,select
from starlette.responses import JSONResponse
from app.core.config import Settings,get_settings
from app.core.errors import AppError
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.agent_platform.models import Dataset,DatasetVersion,TrainingJob
from app.modules.platform.audit import AuditService,redact
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyConflict,IdempotencyService
from app.modules.platform.repositories import AuditLogRepository,IdempotencyRecordRepository
from app.modules.platform.user_schemas import PageMetaData
from app.shared.responses import SuccessResponse

class TrainingJobNotFound(AppError):
 def __init__(self):super().__init__(status_code=404,code="TRAINING_JOB_NOT_FOUND",message="训练任务不存在")
class TrainingDatasetNotReady(AppError):
 def __init__(self):super().__init__(status_code=409,code="TRAINING_DATASET_NOT_READY",message="训练数据集版本尚未就绪")
class TrainingBaseModelNotAllowed(AppError):
 def __init__(self):super().__init__(status_code=409,code="TRAINING_BASE_MODEL_NOT_ALLOWED",message="基座模型不在允许清单中")
class TrainingStateConflict(AppError):
 def __init__(self):super().__init__(status_code=409,code="TRAINING_STATE_CONFLICT",message="训练任务当前状态不允许该操作")

class TrainingConfig(BaseModel):
 model_config=ConfigDict(extra="allow");epochs:int=Field(ge=1,le=10);learning_rate:float=Field(gt=0,le=.1);batch_size:int=Field(ge=1,le=64)
class TrainingCreateRequest(BaseModel):
 model_config=ConfigDict(extra="forbid");dataset_id:UUID;dataset_version:int=Field(ge=1);base_model:str=Field(min_length=3,max_length=200);method:Literal["lora","qlora"];config:TrainingConfig;resource_limits:dict[str,Any]=Field(default_factory=dict)
class TrainingDTO(BaseModel):
 model_config=ConfigDict(extra="forbid",frozen=True);id:UUID;base_model:str;method:str;status:str;progress:int;metrics:dict[str,Any];error_code:str|None;error_message:str|None;created_at:datetime;updated_at:datetime;started_at:datetime|None;finished_at:datetime|None
class TrainingPageDTO(BaseModel):
 model_config=ConfigDict(extra="forbid",frozen=True);items:tuple[TrainingDTO,...];pagination:PageMetaData

class TrainingRepository:
 def __init__(self,session):self.session=session
 async def list(self,page,size):
  total=(await self.session.execute(select(func.count()).select_from(TrainingJob))).scalar_one();rows=tuple((await self.session.execute(select(TrainingJob).order_by(TrainingJob.created_at.desc(),TrainingJob.id.desc()).offset((page-1)*size).limit(size))).scalars().all());return rows,total
 async def get(self,id,lock=False):
  stmt=select(TrainingJob).where(TrainingJob.id==id)
  if lock:stmt=stmt.with_for_update()
  return(await self.session.execute(stmt)).scalar_one_or_none()
 async def ready_version(self,dataset_id,version):
  stmt=select(DatasetVersion).join(Dataset,Dataset.id==DatasetVersion.dataset_id).where(Dataset.id==dataset_id,Dataset.deleted_at.is_(None),DatasetVersion.version==version,DatasetVersion.frozen_at.is_not(None),DatasetVersion.validation_status=="valid",DatasetVersion.contains_sensitive_data.is_(False))
  return(await self.session.execute(stmt)).scalar_one_or_none()
 async def claim(self,limit=1):return tuple((await self.session.execute(select(TrainingJob).where(TrainingJob.status=="queued").order_by(TrainingJob.created_at,TrainingJob.id).limit(limit).with_for_update(skip_locked=True))).scalars().all())
 def add(self,x):self.session.add(x)

def _dto(x):return TrainingDTO(id=x.id,base_model=x.base_model,method=x.method,status=x.status,progress=x.progress,metrics=redact(x.metrics)or{},error_code=x.error_code,error_message="训练任务失败" if x.status=="failed" else None,created_at=x.created_at,updated_at=x.updated_at,started_at=x.started_at,finished_at=x.finished_at)
class TrainingService:
 ACTIVE={"queued","preparing","training","evaluating"}
 def __init__(self,session,repo,idem,audit,allowed,now=None):self.session=session;self.repo=repo;self.idem=idem;self.audit=audit;self.allowed=set(allowed);self.now=now or(lambda:datetime.now(UTC))
 async def list(self,page,size):
  rows,total=await self.repo.list(page,size);return TrainingPageDTO(items=tuple(_dto(x) for x in rows),pagination=PageMetaData(page=page,page_size=size,total=total,total_pages=ceil(total/size)if total else 0))
 async def detail(self,id):
  x=await self.repo.get(id)
  if x is None:raise TrainingJobNotFound()
  return _dto(x)
 async def create(self,actor,payload,key,request_id):
  body=payload.model_dump(mode="json")
  async with self.session.begin():
   d=await self.idem.begin(user_id=actor.user_id,endpoint="POST /api/v1/training-jobs",idempotency_key=key,request_body=body)
   if d.replay:return d.replay.response_status,dict(d.replay.response_body),str(d.replay.response_body["request_id"])
   if d.pending:raise IdempotencyConflict()
   if payload.base_model not in self.allowed or payload.base_model.lower().startswith("deepseek"):raise TrainingBaseModelNotAllowed()
   version=await self.repo.ready_version(payload.dataset_id,payload.dataset_version)
   if version is None:raise TrainingDatasetNotReady()
   now=self.now();x=TrainingJob(id=uuid4(),dataset_version_id=version.id,base_model=payload.base_model,method=payload.method,config=payload.config.model_dump(),resource_limits=payload.resource_limits,status="queued",progress=0,metrics={},created_by=actor.user_id,created_at=now,updated_at=now);self.repo.add(x);data=_dto(x);response=SuccessResponse(data=data,request_id=request_id,timestamp=now).model_dump(mode="json")
   if not await self.idem.complete(record_id=d.record_id,response_status=202,response_body=response,resource_type="training_job",resource_id=str(x.id)):raise IdempotencyConflict()
  return 202,response,request_id
 async def cancel(self,actor,id,key,request_id):
  async with self.session.begin():
   d=await self.idem.begin(user_id=actor.user_id,endpoint=f"POST /api/v1/training-jobs/{id}/cancel",idempotency_key=key,request_body={"id":str(id)})
   if d.replay:return d.replay.response_status,dict(d.replay.response_body),str(d.replay.response_body["request_id"])
   if d.pending:raise IdempotencyConflict()
   x=await self.repo.get(id,lock=True)
   if x is None:raise TrainingJobNotFound()
   if x.status in self.ACTIVE:x.status="cancelled";x.finished_at=self.now();x.updated_at=self.now()
   data=_dto(x);response=SuccessResponse(data=data,request_id=request_id,timestamp=self.now()).model_dump(mode="json");self.audit.record_success(action="training.cancel",resource_type="training_job",resource_id=str(id),request_id=request_id,actor_user_id=actor.user_id,actor_username=actor.username,after_data={"status":x.status})
   if not await self.idem.complete(record_id=d.record_id,response_status=200,response_body=response,resource_type="training_job",resource_id=str(id)):raise IdempotencyConflict()
  return 200,response,request_id

router=APIRouter(prefix="/api/v1/training-jobs",tags=["Training"]);TrainingResponse=SuccessResponse[TrainingDTO];TrainingListResponse=SuccessResponse[TrainingPageDTO]
@asynccontextmanager
async def context(settings):
 db=Database.from_settings(settings)
 try:
  async with db.session() as s:yield TrainingService(s,TrainingRepository(s),IdempotencyService(session=s,repository=IdempotencyRecordRepository(s)),AuditService(AuditLogRepository(s)),(x.strip() for x in settings.local_training_base_models.split(",") if x.strip()))
 finally:await db.dispose()
async def get_service()->AsyncIterator[TrainingService]:
 async with context(get_settings())as s:yield s
def resp(x):status,body,rid=x;return JSONResponse(body,status_code=status,headers={REQUEST_ID_HEADER:rid})
@router.get("",operation_id="listTrainingJobs",response_model=TrainingListResponse)
async def list_(request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("training:read"))],service:Annotated[TrainingService,Depends(get_service)],page:Annotated[int,Query(ge=1)]=1,page_size:Annotated[int,Query(ge=1,le=100)]=20):return SuccessResponse(data=await service.list(page,page_size),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("",operation_id="createTrainingJob",status_code=202,response_model=TrainingResponse)
async def create(payload:TrainingCreateRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("training:run"))],service:Annotated[TrainingService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return resp(await service.create(actor,payload,key,request.state.request_id))
@router.get("/{training_job_id}",operation_id="getTrainingJob",response_model=TrainingResponse)
async def detail(training_job_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("training:read"))],service:Annotated[TrainingService,Depends(get_service)]):return SuccessResponse(data=await service.detail(training_job_id),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("/{training_job_id}/cancel",operation_id="cancelTrainingJob",response_model=TrainingResponse)
async def cancel(training_job_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("training:run"))],service:Annotated[TrainingService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return resp(await service.cancel(actor,training_job_id,key,request.state.request_id))
