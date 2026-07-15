from collections.abc import AsyncIterator
from datetime import UTC,datetime
from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query,Request,Response
from pydantic import BaseModel,ConfigDict,Field,model_validator
from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.ai_knowledge.knowledge import KnowledgeRepository,KnowledgeService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.user_schemas import PageMetaData
from app.shared.responses import SuccessResponse
router=APIRouter(prefix="/api/v1/knowledge-bases",tags=["KnowledgeBases"])
class KBCreate(BaseModel):
 model_config=ConfigDict(extra="forbid");name:str=Field(min_length=2,max_length=100);description:str|None=Field(None,max_length=500);visibility:Literal["public","department","private"];owner_department:str|None=Field(None,max_length=100);embedding_model:Literal["bge-small-zh-v1.5"]="bge-small-zh-v1.5";chunk_size:int=Field(500,ge=100,le=2000);chunk_overlap:int=Field(80,ge=0,le=500);member_user_ids:list[UUID]=[]
 @model_validator(mode="after")
 def valid(self):
  if self.chunk_overlap>=self.chunk_size:raise ValueError("overlap must be smaller than chunk size")
  return self
class KBUpdate(BaseModel):
 model_config=ConfigDict(extra="forbid");version:int=Field(ge=1);name:str|None=Field(None,min_length=2,max_length=100);description:str|None=Field(None,max_length=500);visibility:Literal["public","department","private"]|None=None;owner_department:str|None=Field(None,max_length=100);chunk_size:int|None=Field(None,ge=100,le=2000);chunk_overlap:int|None=Field(None,ge=0,le=500);member_user_ids:list[UUID]|None=None
class KBData(BaseModel):
 model_config=ConfigDict(from_attributes=True,extra="ignore");id:UUID;name:str;description:str|None;visibility:str;owner_user_id:UUID|None;owner_department:str|None;embedding_model:str;chunk_size:int;chunk_overlap:int;collection_name:str;document_count:int=0;members:list=[];created_by:UUID;created_at:datetime;updated_at:datetime;version:int
class KBPage(BaseModel):items:list[KBData];pagination:PageMetaData
async def service_dep()->AsyncIterator[tuple[KnowledgeService,KnowledgeRepository]]:
 db=Database.from_settings(get_settings())
 try:
  async with db.session() as s:repo=KnowledgeRepository(s);yield KnowledgeService(s,repo),repo
 finally:await db.dispose()
def out(kb):return KBData.model_validate(kb)
@router.get("",operation_id="listKnowledgeBases",response_model=SuccessResponse[KBPage])
async def list_kb(request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:read"))],deps:Annotated[tuple,Depends(service_dep)],page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),q:str|None=Query(None,max_length=100),visibility:str|None=None):
 _,repo=deps;rows,total=await repo.list_allowed(user.user_id,user.department,"knowledge:read_all" in user.permissions,page,page_size);items=[out(x) for x in rows if (not q or q.lower() in x.name.lower()) and (not visibility or x.visibility==visibility)];return SuccessResponse(data=KBPage(items=items,pagination=PageMetaData(page=page,page_size=page_size,total=total,total_pages=(total+page_size-1)//page_size)),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("",operation_id="createKnowledgeBase",status_code=201,response_model=SuccessResponse[KBData])
async def create_kb(payload:KBCreate,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:write"))],deps:Annotated[tuple,Depends(service_dep)],_key:Annotated[str,Header(alias="Idempotency-Key",min_length=8,max_length=128)]):
 service,_=deps
 async with service.s.begin():kb=await service.create(user,**payload.model_dump(exclude={"member_user_ids"}))
 return SuccessResponse(data=out(kb),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.get("/{knowledge_base_id}",operation_id="getKnowledgeBase",response_model=SuccessResponse[KBData])
async def get_kb(knowledge_base_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:read"))],deps:Annotated[tuple,Depends(service_dep)]):return SuccessResponse(data=out(await deps[0].require(knowledge_base_id,user)),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.patch("/{knowledge_base_id}",operation_id="updateKnowledgeBase",response_model=SuccessResponse[KBData])
async def update_kb(knowledge_base_id:UUID,payload:KBUpdate,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:write"))],deps:Annotated[tuple,Depends(service_dep)]):
 service,_=deps
 async with service.s.begin():kb=await service.update(knowledge_base_id,user,payload.version,payload.model_dump(exclude={"version","member_user_ids"},exclude_unset=True))
 return SuccessResponse(data=out(kb),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.delete("/{knowledge_base_id}",operation_id="deleteKnowledgeBase",status_code=204)
async def delete_kb(knowledge_base_id:UUID,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:write"))],deps:Annotated[tuple,Depends(service_dep)],version:int=Query(...,ge=1)):
 service,_=deps
 async with service.s.begin():await service.delete(knowledge_base_id,user,version)
 return Response(status_code=204)
