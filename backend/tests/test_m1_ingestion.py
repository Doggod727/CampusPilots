import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.modules.ai_knowledge.ingestion import IngestionWorker


class Repo:
    def __init__(self, claimed): self.claimed=claimed;self.chunks=[]
    async def claim(self): return self.claimed
    async def replace_chunks(self,document,chunks): self.chunks=chunks
class Artifacts:
    def read(self,key): return b"campus guide text"
class Embeddings:
    def embed(self,texts): return tuple((0.1,0.2) for _ in texts)
class Vectors:
    def __init__(self): self.items=[]
    def delete_document(self,*args): pass
    def upsert(self,kb,items): self.items=list(items)


def test_worker_builds_chunks_and_marks_ready():
    doc=SimpleNamespace(id=uuid4(),knowledge_base_id=uuid4(),object_key="x",original_file_name="a.txt",document_version=1,status="pending",page_count=None,chunk_count=0,index_version=None)
    job=SimpleNamespace(stage="queued",progress=0,started_at=None,finished_at=None,error_code=None,error_message=None)
    kb=SimpleNamespace(id=doc.knowledge_base_id,chunk_size=500,chunk_overlap=80)
    repo=Repo((job,doc,kb));vectors=Vectors()
    assert asyncio.run(IngestionWorker(repo,Artifacts(),Embeddings(),vectors).run_once())
    assert doc.status=="ready" and job.stage=="succeeded" and len(repo.chunks)==len(vectors.items)==1


def test_worker_uses_stable_error_without_exception_text():
    class Broken:
        def read(self,key): raise RuntimeError("secret path")
    doc=SimpleNamespace(id=uuid4(),knowledge_base_id=uuid4(),object_key="x",original_file_name="a.txt",document_version=1,status="pending")
    job=SimpleNamespace(stage="queued",progress=0,started_at=None,finished_at=None,error_code=None,error_message="old")
    kb=SimpleNamespace(id=doc.knowledge_base_id,chunk_size=500,chunk_overlap=80)
    assert not asyncio.run(IngestionWorker(Repo((job,doc,kb)),Broken(),Embeddings(),Vectors()).run_once())
    assert job.error_code=="INGESTION_FAILED" and job.error_message is None
