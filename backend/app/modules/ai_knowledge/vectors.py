from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence
from uuid import UUID


@dataclass(frozen=True)
class VectorItem:
    chunk_id: UUID
    text: str
    embedding: tuple[float, ...]
    metadata: dict[str, str | int]


@dataclass(frozen=True)
class VectorHit:
    chunk_id: UUID
    score: float
    metadata: dict[str, str | int]


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class BgeSmallZhEmbeddingProvider:
    """Lazy local provider: importing M1 never loads a model or touches the network."""

    model_name = "BAAI/bge-small-zh-v1.5"

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path or self.model_name
        self._model = None

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_path, local_files_only=True)
        values = self._model.encode(list(texts), normalize_embeddings=True)
        return tuple(tuple(float(value) for value in row) for row in values)


def collection_name(knowledge_base_id: UUID) -> str:
    return f"kb_{knowledge_base_id.hex}"


class ChromaVectorStore:
    def __init__(self, client) -> None:
        self.client = client

    def upsert(self, knowledge_base_id: UUID, items: Sequence[VectorItem]) -> None:
        collection = self.client.get_or_create_collection(collection_name(knowledge_base_id))
        collection.upsert(
            ids=[str(item.chunk_id) for item in items],
            documents=[item.text for item in items],
            embeddings=[list(item.embedding) for item in items],
            metadatas=[item.metadata for item in items],
        )

    def query(self, knowledge_base_id: UUID, embedding: Sequence[float], limit: int) -> tuple[VectorHit, ...]:
        result = self.client.get_or_create_collection(collection_name(knowledge_base_id)).query(
            query_embeddings=[list(embedding)], n_results=limit, include=["distances", "metadatas"]
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadata = result.get("metadatas", [[]])[0]
        return tuple(VectorHit(UUID(identifier), max(0.0, 1.0 - float(distance)), values or {}) for identifier, distance, values in zip(ids, distances, metadata, strict=True))

    def delete_document(self, knowledge_base_id: UUID, document_id: UUID) -> None:
        self.client.get_or_create_collection(collection_name(knowledge_base_id)).delete(where={"document_id": str(document_id)})

    def rebuild(self, knowledge_base_id: UUID, items: Sequence[VectorItem]) -> None:
        name = collection_name(knowledge_base_id)
        try:
            self.client.delete_collection(name)
        except Exception as exc:
            if exc.__class__.__name__ not in {"NotFoundError", "InvalidCollectionException"}:
                raise
        self.upsert(knowledge_base_id, items)
