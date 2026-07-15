import asyncio
from pathlib import Path
from app.modules.ai_knowledge.artifact_store import KnowledgeArtifactInvalid,KnowledgeArtifactStore
class Stream:
 def __init__(self,data):self.data=data
 async def read(self,n):value=self.data[:n];self.data=self.data[n:];return value
def test_store_hash_path_and_delete(tmp_path):
 store=KnowledgeArtifactStore(tmp_path);item=asyncio.run(store.save(Stream(b"hello"),"notes.md"));assert item.size_bytes==5 and item.sha256 and "notes" not in item.object_key;assert store.read(item.object_key)==b"hello";store.delete(item.object_key)
def test_store_rejects_extension_size_and_traversal(tmp_path):
 store=KnowledgeArtifactStore(tmp_path,max_bytes=2)
 for action in (lambda:asyncio.run(store.save(Stream(b"x"),"x.exe")),lambda:asyncio.run(store.save(Stream(b"xxx"),"x.txt")),lambda:store.read("../secret")):
  try:action();assert False
  except KnowledgeArtifactInvalid:pass
