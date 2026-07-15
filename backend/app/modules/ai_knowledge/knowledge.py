from datetime import UTC,datetime
from uuid import UUID,uuid4
from sqlalchemy import func,or_,select,update
from app.core.errors import AppError
from app.modules.ai_knowledge.models import KnowledgeBase,KnowledgeBaseMember,Document
class KnowledgeBaseNotFound(AppError):
 def __init__(self):super().__init__(status_code=404,code="KNOWLEDGE_BASE_NOT_FOUND",message="知识库不存在")
class KnowledgeBaseConflict(AppError):
 def __init__(self,code="RESOURCE_VERSION_CONFLICT"):super().__init__(status_code=409,code=code,message="知识库状态冲突")
class KnowledgeRepository:
 def __init__(self,session):self.s=session
 async def list_allowed(self,user_id,department,global_read,page,page_size):
  member=select(KnowledgeBaseMember.knowledge_base_id).where(KnowledgeBaseMember.user_id==user_id)
  allowed=[KnowledgeBase.owner_user_id==user_id,KnowledgeBase.id.in_(member),KnowledgeBase.visibility=="public"]
  if department:allowed.append((KnowledgeBase.visibility=="department")&(KnowledgeBase.owner_department==department))
  stmt=select(KnowledgeBase).where(KnowledgeBase.deleted_at.is_(None),or_(*allowed) if not global_read else True).order_by(KnowledgeBase.updated_at.desc(),KnowledgeBase.id)
  total=(await self.s.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one();rows=(await self.s.execute(stmt.offset((page-1)*page_size).limit(page_size))).scalars().all();return list(rows),total
 async def get(self,kb_id):return(await self.s.execute(select(KnowledgeBase).where(KnowledgeBase.id==kb_id,KnowledgeBase.deleted_at.is_(None)))).scalar_one_or_none()
 async def access(self,kb_id,user_id):return(await self.s.execute(select(KnowledgeBaseMember.access_level).where(KnowledgeBaseMember.knowledge_base_id==kb_id,KnowledgeBaseMember.user_id==user_id))).scalar_one_or_none()
 async def has_documents(self,kb_id):return bool((await self.s.execute(select(func.count(Document.id)).where(Document.knowledge_base_id==kb_id,Document.deleted_at.is_(None)))).scalar_one())
 def add(self,kb):self.s.add(kb)
 async def update_version(self,kb_id,version,values):return(await self.s.execute(update(KnowledgeBase).where(KnowledgeBase.id==kb_id,KnowledgeBase.version==version,KnowledgeBase.deleted_at.is_(None)).values(**values,version=version+1,updated_at=datetime.now(UTC)))).rowcount==1
class KnowledgeService:
 def __init__(self,session,repo):self.s=session;self.r=repo
 async def require(self,kb_id,user,write=False):
  kb=await self.r.get(kb_id)
  if not kb:raise KnowledgeBaseNotFound()
  global_ok=("knowledge:write" if write else "knowledge:read") in user.permissions
  member=await self.r.access(kb_id,user.user_id)
  readable=global_ok or kb.owner_user_id==user.user_id or member in {"viewer","editor","owner"} or kb.visibility=="public" or (kb.visibility=="department" and kb.owner_department==user.department)
  writable=global_ok or kb.owner_user_id==user.user_id or member in {"editor","owner"}
  if not (writable if write else readable):raise KnowledgeBaseNotFound()
  return kb
 async def create(self,user,**values):
  kb=KnowledgeBase(id=uuid4(),created_by=user.user_id,owner_user_id=values.pop("owner_user_id",None) or user.user_id,collection_name=f"kb_{uuid4().hex}",version=1,**values);self.r.add(kb);return kb
 async def update(self,kb_id,user,version,values):
  await self.require(kb_id,user,True)
  if not await self.r.update_version(kb_id,version,values):raise KnowledgeBaseConflict()
  return await self.r.get(kb_id)
 async def delete(self,kb_id,user,version):
  await self.require(kb_id,user,True)
  if await self.r.has_documents(kb_id):raise KnowledgeBaseConflict("KNOWLEDGE_BASE_IN_USE")
  if not await self.r.update_version(kb_id,version,{"deleted_at":datetime.now(UTC)}):raise KnowledgeBaseConflict()
