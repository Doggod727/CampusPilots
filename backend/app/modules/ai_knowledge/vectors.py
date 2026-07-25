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


class LazyChromaClient:
    """Create the local persistent Chroma client only when M1 actually uses it.

    Runtime composition and liveness must remain importable even when the optional
    AI dependencies are absent. Readiness and the first real vector operation still
    fail closed, so a deployment cannot silently fall back to mock retrieval.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._client = None

    def _get(self):
        if self._client is None:
            import chromadb

            self._client = chromadb.PersistentClient(path=self.path)
        return self._client

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


class LazyOpenSearchClient:
    """延迟初始化 OpenSearch 客户端（惰性启动纪律）。

    与 LazyChromaClient 相同模式：导入时不连接，首次使用时才建立连接。
    保证 /health/live 和应用导入不依赖 OpenSearch 可用性。
    """

    def __init__(self, url: str, username: str, password: str) -> None:
        self.url = url
        self.username = username
        self.password = password
        self._client = None

    def _get(self):
        if self._client is None:
            from opensearchpy import OpenSearch

            self._client = OpenSearch(
                hosts=[self.url],
                http_auth=(self.username, self.password),
                use_ssl=False,
                verify_certs=False,
                timeout=30,
            )
        return self._client

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


class OpenSearchVectorStore:
    """OpenSearch 向量存储，支持 BM25 + 向量混合检索。

    与 ChromaVectorStore 接口兼容，额外提供 hybrid_query 方法实现混合检索。
    使用 IK analyzer 处理中文分词，knn_vector 字段存储 embedding。
    """

    def __init__(self, client, index_prefix: str = "kb_") -> None:
        self.client = client
        self.index_prefix = index_prefix

    def _index_name(self, knowledge_base_id: UUID) -> str:
        return f"{self.index_prefix}{knowledge_base_id.hex}"

    def _create_index(self, knowledge_base_id: UUID) -> None:
        """创建索引，配置 IK analyzer 和 knn_vector"""
        index_name = self._index_name(knowledge_base_id)
        if self.client.indices.exists(index=index_name):
            return

        self.client.indices.create(
            index=index_name,
            body={
                "settings": {
                    "index": {
                        "number_of_shards": 1,
                        "number_of_replicas": 0,
                    }
                },
                "mappings": {
                    "properties": {
                        "text": {
                            "type": "text",
                            "analyzer": "smartcn",  # 官方中文分词
                            "search_analyzer": "smartcn",
                        },
                        "embedding": {
                            "type": "float",
                        },
                        "document_id": {"type": "keyword"},
                        "document_version": {"type": "integer"},
                        "status": {"type": "keyword"},
                    }
                },
            },
        )

    def upsert(self, knowledge_base_id: UUID, items: Sequence[VectorItem]) -> None:
        """批量索引文档到 OpenSearch"""
        if not items:
            return

        self._create_index(knowledge_base_id)
        index_name = self._index_name(knowledge_base_id)

        operations = []
        for item in items:
            operations.append({"index": {"_index": index_name, "_id": str(item.chunk_id)}})
            operations.append({
                "text": item.text,
                "embedding": list(item.embedding),
                **item.metadata,
            })

        if operations:
            self.client.bulk(body=operations, refresh=True)

    def query(self, knowledge_base_id: UUID, embedding: Sequence[float], limit: int) -> tuple[VectorHit, ...]:
        """纯向量检索（向后兼容 ChromaVectorStore 接口）"""
        return self.hybrid_query(knowledge_base_id, embedding, None, limit)

    def hybrid_query(
        self,
        knowledge_base_id: UUID,
        embedding: Sequence[float],
        text: str | None,
        limit: int,
    ) -> tuple[VectorHit, ...]:
        """混合检索：BM25 + 向量，使用 OpenSearch hybrid query（RRF 融合）。

        Args:
            knowledge_base_id: 知识库 ID
            embedding: 查询向量（512 维）
            text: 查询文本（用于 BM25），为 None 时只做向量检索
            limit: 返回结果数量

        Returns:
            VectorHit 元组，按分数降序排列
        """
        index_name = self._index_name(knowledge_base_id)
        if not self.client.indices.exists(index=index_name):
            return ()

        # 向量查询（Painless 脚本计算余弦相似度，不需要 KNN 插件）
        vector_query = {
            "script_score": {
                "query": {"match_all": {}},
                "script": {
                    "source": """
                        double dotProduct = 0.0;
                        double normA = 0.0;
                        double normB = 0.0;
                        for (int i = 0; i < params.query_vector.length; i++) {
                            dotProduct += params.query_vector[i] * doc['embedding'][i];
                            normA += params.query_vector[i] * params.query_vector[i];
                            normB += doc['embedding'][i] * doc['embedding'][i];
                        }
                        return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
                    """,
                    "params": {"query_vector": list(embedding)},
                },
            }
        }

        if text:
            # 混合检索：BM25 + 向量（使用 bool query 组合）
            bm25_query = {
                "match": {
                    "text": {
                        "query": text,
                        "analyzer": "smartcn",
                    }
                }
            }

            # 使用 function_score 组合 BM25 和向量分数
            body = {
                "query": {
                    "bool": {
                        "should": [
                            bm25_query,
                            vector_query,
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "size": limit,
                "_source": ["document_id", "document_version", "status"],
            }
        else:
            # 纯向量检索
            body = {
                "query": vector_query,
                "size": limit,
                "_source": ["document_id", "document_version", "status"],
            }

        try:
            response = self.client.search(index=index_name, body=body)
            hits = response["hits"]["hits"]

            # 归一化分数到 0-1 范围（BM25 分数无界，需要归一化）
            max_score = max((hit["_score"] for hit in hits), default=1.0)
            if max_score <= 0:
                max_score = 1.0

            return tuple(
                VectorHit(
                    chunk_id=UUID(hit["_id"]),
                    score=min(1.0, float(hit["_score"]) / max_score),  # 归一化到 0-1
                    metadata=hit.get("_source", {}),
                )
                for hit in hits
            )
        except Exception:
            # 索引不存在或查询失败时返回空结果（fail-closed）
            return ()

    def delete_document(self, knowledge_base_id: UUID, document_id: UUID) -> None:
        """删除指定文档的所有 chunks"""
        index_name = self._index_name(knowledge_base_id)
        if not self.client.indices.exists(index=index_name):
            return

        try:
            self.client.delete_by_query(
                index=index_name,
                body={
                    "query": {
                        "term": {"document_id": str(document_id)}
                    }
                },
                refresh=True,
            )
        except Exception:
            # 文档不存在时忽略错误
            pass

    def rebuild(self, knowledge_base_id: UUID, items: Sequence[VectorItem]) -> None:
        """重建索引：删除旧索引，重新创建并批量索引"""
        index_name = self._index_name(knowledge_base_id)
        if self.client.indices.exists(index=index_name):
            self.client.indices.delete(index=index_name)
        self.upsert(knowledge_base_id, items)


def create_vector_store(settings):
    """根据配置创建向量存储（Chroma 或 OpenSearch）"""
    if settings.knowledge_search_backend == "opensearch":
        client = LazyOpenSearchClient(
            url=settings.knowledge_opensearch_url,
            username=settings.knowledge_opensearch_username,
            password=settings.knowledge_opensearch_password.get_secret_value(),
        )
        return OpenSearchVectorStore(client, settings.knowledge_opensearch_index_prefix)
    else:
        client = LazyChromaClient(str(settings.knowledge_chroma_path))
        return ChromaVectorStore(client)
