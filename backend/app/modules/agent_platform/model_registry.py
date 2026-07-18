from __future__ import annotations
import asyncio,hashlib,re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC,datetime
from pathlib import Path,PurePosixPath
from typing import Annotated,Any,Literal
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,Header,Query,Request
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import func,select,update
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse
from app.core.config import Settings,get_settings
from app.core.errors import AppError
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.agent_platform.models import EvaluationJob,ModelVersion
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyConflict,IdempotencyService
from app.modules.platform.repositories import AuditLogRepository,IdempotencyRecordRepository
from app.shared.responses import SuccessResponse

class ModelNotFound(AppError):
 def __init__(self):super().__init__(status_code=404,code="MODEL_NOT_FOUND",message="模型版本不存在")
class ModelEvaluationRequired(AppError):
 def __init__(self):super().__init__(status_code=409,code="MODEL_EVALUATION_REQUIRED",message="模型尚未通过评估")
class ModelFallbackRequired(AppError):
 def __init__(self):super().__init__(status_code=409,code="MODEL_FALLBACK_REQUIRED",message="复杂生成必须保留DeepSeek活动兜底")
class ModelStateConflict(AppError):
 def __init__(self):super().__init__(status_code=409,code="MODEL_STATE_CONFLICT",message="模型当前状态不允许该操作")
class DuplicateModel(AppError):
 def __init__(self):super().__init__(status_code=409,code="DUPLICATE_RESOURCE",message="模型名称和版本已存在")
class ModelArtifactInvalid(AppError):
 def __init__(self):super().__init__(status_code=409,code="MODEL_ARTIFACT_INVALID",message="模型产物无效")

class ModelRegisterRequest(BaseModel):
 model_config=ConfigDict(extra="forbid");name:str=Field(min_length=2,max_length=100);purpose:Literal["complex_generation","agent_router","rag_reranker","embedding"];provider:Literal["deepseek","local","rule"];base_model:str=Field(min_length=2,max_length=200);version:str=Field(min_length=1,max_length=50);quantization:str|None=Field(default=None,max_length=30);artifact_key:str|None=Field(default=None,max_length=500);artifact_sha256:str|None=Field(default=None,pattern=r"^[0-9a-f]{64}$");training_job_id:UUID|None=None;config:dict[str,Any]
 @model_validator(mode="after")
 def provider_fields(self):
  if self.provider=="local" and(not self.artifact_key or not self.artifact_sha256):raise ValueError("local model requires artifact key and hash")
  if self.provider=="deepseek" and self.config.get("api_key_env")!="DEEPSEEK_API_KEY":raise ValueError("DeepSeek must reference DEEPSEEK_API_KEY")
  if any(re.search(r"secret|token|api[_-]?key$|password",str(k),re.I) and k!="api_key_env" for k in self.config):raise ValueError("secret values are not accepted")
  return self
class ModelDTO(BaseModel):
 model_config=ConfigDict(extra="forbid",frozen=True);id:UUID;name:str;purpose:str;provider:str;base_model:str;version:str;quantization:str|None;artifact_sha256:str|None;config:dict[str,Any];metrics:dict[str,Any];status:str;created_at:datetime;activated_at:datetime|None
class ModelListDTO(BaseModel):
 model_config=ConfigDict(extra="forbid",frozen=True);items:tuple[ModelDTO,...]

def _clean(value):
 if isinstance(value,dict):return {k:_clean(v)for k,v in value.items()if not re.search(r"password|token|authorization|cookie|api[_-]?key|secret",k,re.I)}
 if isinstance(value,list):return[_clean(x)for x in value]
 return value
def dto(x):return ModelDTO(id=x.id,name=x.name,purpose=x.purpose,provider=x.provider,base_model=x.base_model,version=x.version,quantization=x.quantization,artifact_sha256=x.artifact_sha256,config=_clean(x.config),metrics=_clean(x.metrics),status=x.status,created_at=x.created_at,activated_at=x.activated_at)
class ModelRepository:
 def __init__(self,s):self.s=s
 async def list(self,purpose=None):
  stmt=select(ModelVersion)
  if purpose:stmt=stmt.where(ModelVersion.purpose==purpose)
  return tuple((await self.s.execute(stmt.order_by(ModelVersion.purpose,ModelVersion.name,ModelVersion.version))).scalars().all())
 async def get(self,id,lock=False):
  stmt=select(ModelVersion).where(ModelVersion.id==id)
  if lock:stmt=stmt.with_for_update()
  return(await self.s.execute(stmt)).scalar_one_or_none()
 async def duplicate(self,name,version):return(await self.s.execute(select(ModelVersion.id).where(ModelVersion.name==name,ModelVersion.version==version))).scalar_one_or_none()is not None
 async def evaluated(self,id):return(await self.s.execute(select(func.count()).select_from(EvaluationJob).where(EvaluationJob.target_type=="model",EvaluationJob.target_id==id,EvaluationJob.status=="succeeded"))).scalar_one()>0
 async def training_job(self,id):
  from app.modules.agent_platform.models import TrainingJob
  return(await self.s.execute(select(TrainingJob).where(TrainingJob.id==id))).scalar_one_or_none()
 async def deactivate_purpose(self,purpose,except_id,now):await self.s.execute(update(ModelVersion).where(ModelVersion.purpose==purpose,ModelVersion.status=="active",ModelVersion.id!=except_id).values(status="inactive",activated_at=None))
 def add(self,x):self.s.add(x)

async def verify_artifact(root:Path,key:str,digest:str):
 pure=PurePosixPath(key)
 if pure.is_absolute()or".."in pure.parts:raise ModelArtifactInvalid()
 path=(root/Path(*pure.parts)).resolve();resolved=root.resolve()
 if resolved not in path.parents or not path.is_file()or path.is_symlink():raise ModelArtifactInvalid()
 actual=await asyncio.to_thread(lambda:hashlib.sha256(path.read_bytes()).hexdigest())
 if actual!=digest:raise ModelArtifactInvalid()

class ModelService:
 def __init__(self,s,repo,idem,audit,root,now=None):self.s=s;self.r=repo;self.idem=idem;self.audit=audit;self.root=root;self.now=now or(lambda:datetime.now(UTC))
 async def list(self,purpose=None):return ModelListDTO(items=tuple(dto(x)for x in await self.r.list(purpose)))
 async def detail(self,id):
  x=await self.r.get(id)
  if x is None:raise ModelNotFound()
  return dto(x)
 async def register(self,actor,p,key,rid):
  body=p.model_dump(mode="json")
  async with self.s.begin():
   d=await self.idem.begin(user_id=actor.user_id,endpoint="POST /api/v1/models",idempotency_key=key,request_body=body)
   if d.replay:return d.replay.response_status,dict(d.replay.response_body),str(d.replay.response_body["request_id"])
   if d.pending:raise IdempotencyConflict()
   if p.training_job_id is not None:
    job=await self.r.training_job(p.training_job_id)
    # 产物归属校验：训练任务必须成功完成且 artifact_key 正是该任务登记产物
    if job is None or job.status!="succeeded" or not job.artifact_key or job.artifact_key!=p.artifact_key:raise ModelArtifactInvalid()
   if p.provider=="local":await verify_artifact(self.root,p.artifact_key,p.artifact_sha256)
   if await self.r.duplicate(p.name,p.version):raise DuplicateModel()
   now=self.now();x=ModelVersion(id=uuid4(),name=p.name,purpose=p.purpose,provider=p.provider,base_model=p.base_model,version=p.version,quantization=p.quantization,artifact_key=p.artifact_key,artifact_sha256=p.artifact_sha256,config=p.config,metrics={},status="candidate",training_job_id=p.training_job_id,created_by=actor.user_id,created_at=now);self.r.add(x);data=dto(x);response=SuccessResponse(data=data,request_id=rid,timestamp=now).model_dump(mode="json")
   if not await self.idem.complete(record_id=d.record_id,response_status=201,response_body=response,resource_type="model",resource_id=str(x.id)):raise IdempotencyConflict()
  return 201,response,rid
 async def change(self,actor,id,action,key,rid):
  try:
   async with self.s.begin():
    d=await self.idem.begin(user_id=actor.user_id,endpoint=f"POST /api/v1/models/{id}/{action}",idempotency_key=key,request_body={"id":str(id),"action":action})
    if d.replay:return d.replay.response_status,dict(d.replay.response_body),str(d.replay.response_body["request_id"])
    if d.pending:raise IdempotencyConflict()
    x=await self.r.get(id,lock=True)
    if x is None:raise ModelNotFound()
    before=x.status
    if action=="activate":
     if x.status!="active":
      if not await self.r.evaluated(id):raise ModelEvaluationRequired()
      if x.purpose=="complex_generation"and x.provider!="deepseek":raise ModelFallbackRequired()
      await self.r.deactivate_purpose(x.purpose,x.id,self.now());x.status="active";x.activated_at=self.now()
    else:
     if x.status=="active"and x.purpose=="complex_generation":raise ModelFallbackRequired()
     if x.status=="active":x.status="inactive";x.activated_at=None
    data=dto(x);response=SuccessResponse(data=data,request_id=rid,timestamp=self.now()).model_dump(mode="json");self.audit.record_success(action=f"model.{action}",resource_type="model",resource_id=str(id),request_id=rid,actor_user_id=actor.user_id,actor_username=actor.username,before_data={"status":before},after_data={"status":x.status})
    if not await self.idem.complete(record_id=d.record_id,response_status=200,response_body=response,resource_type="model",resource_id=str(id)):raise IdempotencyConflict()
  except IntegrityError as exc:
   if "uq_model_one_active_purpose" in str(exc):raise ModelStateConflict() from exc
   raise
  return 200,response,rid

router=APIRouter(prefix="/api/v1/models",tags=["Models"]);ModelResponse=SuccessResponse[ModelDTO];ModelListResponse=SuccessResponse[ModelListDTO]
@asynccontextmanager
async def context(settings):
 db=Database.from_settings(settings)
 try:
  async with db.session()as s:yield ModelService(s,ModelRepository(s),IdempotencyService(session=s,repository=IdempotencyRecordRepository(s)),AuditService(AuditLogRepository(s)),settings.model_artifact_root)
 finally:await db.dispose()
async def get_service()->AsyncIterator[ModelService]:
 async with context(get_settings())as s:yield s
def response(x):status,body,rid=x;return JSONResponse(body,status_code=status,headers={REQUEST_ID_HEADER:rid})
@router.get("",operation_id="listModelVersions",response_model=ModelListResponse)
async def list_(request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("model:read"))],service:Annotated[ModelService,Depends(get_service)],purpose:Literal["complex_generation","agent_router","rag_reranker","embedding"]|None=None):return SuccessResponse(data=await service.list(purpose),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("",operation_id="registerModelVersion",status_code=201,response_model=ModelResponse)
async def register(payload:ModelRegisterRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("model:write"))],service:Annotated[ModelService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return response(await service.register(actor,payload,key,request.state.request_id))
@router.get("/{model_id}",operation_id="getModelVersion",response_model=ModelResponse)
async def detail(model_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("model:read"))],service:Annotated[ModelService,Depends(get_service)]):return SuccessResponse(data=await service.detail(model_id),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("/{model_id}/activate",operation_id="activateModelVersion",response_model=ModelResponse)
async def activate(model_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("model:activate"))],service:Annotated[ModelService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return response(await service.change(actor,model_id,"activate",key,request.state.request_id))
@router.post("/{model_id}/deactivate",operation_id="deactivateModelVersion",response_model=ModelResponse)
async def deactivate(model_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_permissions("model:activate"))],service:Annotated[ModelService,Depends(get_service)],key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]):return response(await service.change(actor,model_id,"deactivate",key,request.state.request_id))
