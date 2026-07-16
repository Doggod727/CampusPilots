from collections.abc import AsyncIterator
from datetime import UTC,datetime
from typing import Annotated
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,File,Header,Query,Request,Response,UploadFile
from sqlalchemy import func,select
from app.core.config import get_settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.ai_knowledge.artifact_store import KnowledgeArtifactStore
from app.modules.ai_knowledge.knowledge import KnowledgeRepository,KnowledgeService,KnowledgeBaseNotFound
from app.modules.ai_knowledge.models import Document,DocumentChunk,IngestionJob
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.shared.responses import SuccessResponse
router=APIRouter(tags=["Documents"])
def error(code,status=409):return AppError(status_code=status,code=code,message="文档状态无效")
async def deps():
 settings=get_settings();db=Database.from_settings(settings)
 try:
  async with db.session() as s:yield s,KnowledgeService(s,KnowledgeRepository(s)),KnowledgeArtifactStore(settings.knowledge_upload_root,settings.knowledge_max_file_bytes)
 finally:await db.dispose()
def doc(x):return {k:getattr(x,k) for k in ("id","knowledge_base_id","title","original_file_name","mime_type","file_size_bytes","file_sha256","status","document_version","index_version","page_count","chunk_count","published_at","inactive_at","expires_at","created_by","version","created_at","updated_at")}
async def owned(s,service,user,id,write=False):
 d=(await s.execute(select(Document).where(Document.id==id,Document.deleted_at.is_(None)))).scalar_one_or_none()
 if not d:raise error("DOCUMENT_NOT_FOUND",404)
 await service.require(d.knowledge_base_id,user,write);return d
@router.get("/api/v1/knowledge-bases/{knowledge_base_id}/documents",operation_id="listDocuments")
async def list_docs(knowledge_base_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:read"))],x=Depends(deps),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),status:str|None=None):
 s,service,_=x;await service.require(knowledge_base_id,user);q=select(Document).where(Document.knowledge_base_id==knowledge_base_id,Document.deleted_at.is_(None));q=q.where(Document.status==status) if status else q;total=(await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one();rows=(await s.execute(q.order_by(Document.updated_at.desc()).offset((page-1)*page_size).limit(page_size))).scalars().all();return SuccessResponse(data={"items":[doc(d) for d in rows],"pagination":{"page":page,"page_size":page_size,"total":total,"total_pages":((total+page_size-1)//page_size)}},request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("/api/v1/knowledge-bases/{knowledge_base_id}/documents",operation_id="uploadDocuments",status_code=202)
async def upload(knowledge_base_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:write"))],files:Annotated[list[UploadFile],File(min_length=1,max_length=10)],idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)],x=Depends(deps)):
 s,service,store=x;await service.require(knowledge_base_id,user,True);items=[]
 for f in files:
  a=await store.save(f,f.filename or "upload");d=Document(id=uuid4(),knowledge_base_id=knowledge_base_id,title=(f.filename or "文档")[:200],original_file_name=(f.filename or "upload")[:255],object_key=a.object_key,mime_type=f.content_type or "application/octet-stream",file_size_bytes=a.size_bytes,file_sha256=a.sha256,status="pending",created_by=user.user_id,version=1);s.add(d);j=IngestionJob(id=uuid4(),document_id=d.id,stage="queued",progress=0,attempt=1,max_attempts=3,created_by=user.user_id);s.add(j);items.append({"document":doc(d),"job_id":j.id})
 await s.commit()
 return SuccessResponse(data={"items":items},request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.get("/api/v1/documents/{document_id}",operation_id="getDocument")
async def get(document_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:read"))],x=Depends(deps)):return SuccessResponse(data=doc(await owned(x[0],x[1],user,document_id)),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.delete("/api/v1/documents/{document_id}",operation_id="deleteDocument",status_code=204)
async def delete(document_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:write"))],x=Depends(deps)):
 d=await owned(x[0],x[1],user,document_id,True);d.status="deleted";d.deleted_at=datetime.now(UTC);await x[0].commit();return Response(status_code=204)
@router.get("/api/v1/documents/{document_id}/chunks",operation_id="listDocumentChunks")
async def chunks(document_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:read"))],x=Depends(deps),page:int=1,page_size:int=20):
 await owned(x[0],x[1],user,document_id);rows=(await x[0].execute(select(DocumentChunk).where(DocumentChunk.document_id==document_id).order_by(DocumentChunk.chunk_index).offset((page-1)*page_size).limit(page_size))).scalars().all();return SuccessResponse(data={"items":[{"id":c.id,"chunk_index":c.chunk_index,"content":c.content,"source_location":c.source_location,"page_number":c.page_number} for c in rows],"pagination":{"page":page,"page_size":page_size,"total":len(rows),"total_pages":1 if rows else 0}},request_id=request.state.request_id,timestamp=datetime.now(UTC))
async def transition(id,user,x,target):
 d=await owned(x[0],x[1],user,id,True)
 if target=="published" and d.status!="ready":raise error("DOCUMENT_NOT_READY")
 d.status=target;d.published_at=datetime.now(UTC) if target=="published" else d.published_at;d.inactive_at=datetime.now(UTC) if target=="inactive" else None;return d
@router.post("/api/v1/documents/{document_id}/publish",operation_id="publishDocument")
async def publish(document_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:publish"))],x=Depends(deps)):
 d=await transition(document_id,user,x,"published");await x[0].commit();return SuccessResponse(data=doc(d),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("/api/v1/documents/{document_id}/deactivate",operation_id="deactivateDocument")
async def deactivate(document_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:publish"))],x=Depends(deps)):
 d=await transition(document_id,user,x,"inactive");await x[0].commit();return SuccessResponse(data=doc(d),request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.get("/api/v1/ingestion-jobs/{job_id}",operation_id="getIngestionJob")
async def job(job_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:read"))],x=Depends(deps)):
 j=(await x[0].execute(select(IngestionJob).where(IngestionJob.id==job_id))).scalar_one_or_none()
 if not j:raise error("INGESTION_JOB_NOT_FOUND",404)
 await owned(x[0],x[1],user,j.document_id);return SuccessResponse(data={"id":j.id,"document_id":j.document_id,"stage":j.stage,"progress":j.progress,"attempt":j.attempt,"error_code":j.error_code},request_id=request.state.request_id,timestamp=datetime.now(UTC))
@router.post("/api/v1/ingestion-jobs/{job_id}/retry",operation_id="retryIngestionJob",status_code=202)
async def retry(job_id:UUID,request:Request,user:Annotated[AuthenticatedUser,Depends(require_permissions("knowledge:write"))],x=Depends(deps)):
 j=(await x[0].execute(select(IngestionJob).where(IngestionJob.id==job_id).with_for_update())).scalar_one_or_none()
 if not j or j.stage!="failed" or j.attempt>=j.max_attempts:raise error("INGESTION_RETRY_NOT_ALLOWED")
 await owned(x[0],x[1],user,j.document_id,True);j.stage="queued";j.progress=0;j.attempt+=1;j.error_code=None;await x[0].commit();return SuccessResponse(data={"id":j.id,"stage":j.stage,"attempt":j.attempt},request_id=request.state.request_id,timestamp=datetime.now(UTC))
