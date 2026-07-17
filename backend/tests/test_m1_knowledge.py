import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.ai_knowledge.knowledge import (
    KnowledgeBaseConflict,
    KnowledgeBaseNotFound,
    KnowledgeService,
)
def test_m1_knowledge_errors_are_stable():
 assert KnowledgeBaseNotFound().code=="KNOWLEDGE_BASE_NOT_FOUND" and KnowledgeBaseConflict().status_code==409


class _Repository:
 def __init__(self, knowledge_base): self.knowledge_base = knowledge_base
 async def get(self, _knowledge_base_id): return self.knowledge_base
 async def access(self, _knowledge_base_id, _user_id): return None
 async def get_allowed(self, _knowledge_base_id, user_id, _department, *, global_access, write=False, for_update=False):
  if global_access or self.knowledge_base.owner_user_id == user_id: return self.knowledge_base
  return None


def _private_knowledge_base():
 return SimpleNamespace(owner_user_id=uuid4(), visibility="private", owner_department=None)


def test_capability_permission_does_not_bypass_private_resource_scope():
 service = KnowledgeService(None, _Repository(_private_knowledge_base()))
 user = SimpleNamespace(user_id=uuid4(), permissions=("knowledge:read",), department=None)

 with pytest.raises(KnowledgeBaseNotFound):
  asyncio.run(service.require(uuid4(), user))


def test_explicit_global_permission_can_read_private_resource():
 knowledge_base = _private_knowledge_base()
 service = KnowledgeService(None, _Repository(knowledge_base))
 user = SimpleNamespace(user_id=uuid4(), permissions=("knowledge:read_all",), department=None)

 assert asyncio.run(service.require(uuid4(), user)) is knowledge_base
