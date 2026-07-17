from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai_knowledge.models import Document, DocumentChunk, IngestionJob, KnowledgeBase
from app.modules.ai_knowledge.parsing import chunk_document, parse_document
from app.modules.ai_knowledge.vectors import VectorItem


class IngestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self) -> tuple[IngestionJob, Document, KnowledgeBase] | None:
        statement = select(IngestionJob).where(IngestionJob.stage == "queued").order_by(IngestionJob.created_at).with_for_update(skip_locked=True).limit(1)
        job = (await self.session.execute(statement)).scalar_one_or_none()
        if job is None:
            return None
        document = await self.session.get(Document, job.document_id)
        knowledge_base = await self.session.get(KnowledgeBase, document.knowledge_base_id)
        return job, document, knowledge_base

    async def replace_chunks(self, document: Document, chunks: list[DocumentChunk]) -> None:
        await self.session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id, DocumentChunk.document_version == document.document_version))
        self.session.add_all(chunks)


class IngestionWorker:
    parser_version = "m1-parser-v1"

    def __init__(self, repository: IngestionRepository, artifact_store, embedding_provider, vector_store, now=lambda: datetime.now(UTC)) -> None:
        self.repository = repository
        self.artifacts = artifact_store
        self.embeddings = embedding_provider
        self.vectors = vector_store
        self.now = now

    async def run_once(self) -> bool:
        claimed = await self.repository.claim()
        if claimed is None:
            return False
        job, document, knowledge_base = claimed
        job.started_at = self.now()
        document.status = "processing"
        try:
            job.stage, job.progress = "parsing", 10
            parsed = parse_document(self.artifacts.read(document.object_key), document.original_file_name)
            job.stage, job.progress = "splitting", 35
            chunks = chunk_document(parsed, knowledge_base.chunk_size, knowledge_base.chunk_overlap)
            job.stage, job.progress = "embedding", 55
            embeddings = self.embeddings.embed([chunk.content for chunk in chunks])
            index_version = document.document_version
            entities = [DocumentChunk(id=uuid4(), document_id=document.id, document_version=document.document_version, chunk_index=chunk.index, content=chunk.content, source_location=chunk.heading or document.original_file_name, page_number=chunk.page_number, token_count=max(1, len(chunk.content) // 2), content_sha256=hashlib.sha256(chunk.content.encode()).hexdigest(), clean_status="clean", vector_id=f"{document.id}:{document.document_version}:{chunk.index}", index_version=index_version) for chunk in chunks]
            await self.repository.replace_chunks(document, entities)
            job.stage, job.progress = "indexing", 80
            self.vectors.delete_document(knowledge_base.id, document.id)
            self.vectors.upsert(knowledge_base.id, [VectorItem(entity.id, entity.content, tuple(vector), {"document_id": str(document.id), "document_version": document.document_version, "status": "ready"}) for entity, vector in zip(entities, embeddings, strict=True)])
            document.page_count = len(parsed.sections)
            document.chunk_count = len(entities)
            document.index_version = index_version
            document.status = "ready"
            job.stage, job.progress, job.finished_at = "succeeded", 100, self.now()
            return True
        except Exception as exc:
            job.stage, job.finished_at = "failed", self.now()
            job.error_code = getattr(exc, "code", "INGESTION_FAILED")
            job.error_message = None
            document.status = "failed"
            return False
