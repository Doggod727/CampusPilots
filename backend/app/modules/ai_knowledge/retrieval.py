from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai_knowledge.models import Document, DocumentChunk


@dataclass(frozen=True)
class RetrievalCitation:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    content: str
    source_location: str
    page_number: int | None
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    citations: tuple[RetrievalCitation, ...]
    confidence: float


class RetrievalService:
    def __init__(self, session: AsyncSession, knowledge_service, embedding_provider, vector_store, threshold: float = 0.62) -> None:
        self.session = session
        self.knowledge = knowledge_service
        self.embeddings = embedding_provider
        self.vectors = vector_store
        self.threshold = threshold

    async def authorized_knowledge_bases(self, user, limit: int = 20) -> list[UUID]:
        """Compute the knowledge bases visible to the user (tool scope when ids are omitted)."""

        rows, _ = await self.knowledge.r.list_allowed(
            user.user_id,
            getattr(user, "department", None),
            "knowledge:read_all" in user.permissions,
            1,
            limit,
        )
        return [row.id for row in rows]

    async def search(self, user, query: str, knowledge_base_ids: list[UUID], top_k: int = 6) -> RetrievalResult:
        authorized: list[UUID] = []
        for knowledge_base_id in dict.fromkeys(knowledge_base_ids):
            try:
                await self.knowledge.require(knowledge_base_id, user)
                authorized.append(knowledge_base_id)
            except Exception as exc:
                if getattr(exc, "status_code", None) not in {403, 404}:
                    raise
        if not authorized:
            return RetrievalResult((), 0.0)
        vector = self.embeddings.embed([query])[0]
        scores = {}
        for knowledge_base_id in authorized:
            for hit in self.vectors.query(knowledge_base_id, vector, top_k):
                if hit.score >= self.threshold:
                    scores[hit.chunk_id] = max(scores.get(hit.chunk_id, 0.0), hit.score)
        if not scores:
            return RetrievalResult((), 0.0)
        statement = select(DocumentChunk, Document).join(Document, Document.id == DocumentChunk.document_id).where(
            DocumentChunk.id.in_(scores),
            Document.knowledge_base_id.in_(authorized),
            Document.status == "published",
            Document.deleted_at.is_(None),
            DocumentChunk.index_version == Document.index_version,
        )
        rows = (await self.session.execute(statement)).all()
        citations = [RetrievalCitation(chunk.id, document.id, document.title, chunk.content, chunk.source_location, chunk.page_number, scores[chunk.id]) for chunk, document in rows]
        citations.sort(key=lambda item: (-item.score, str(item.chunk_id)))
        selected = tuple(citations[:top_k])
        return RetrievalResult(selected, selected[0].score if selected else 0.0)
