from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.ai_knowledge.artifact_store import KnowledgeArtifactStore, StoredArtifact
from app.modules.ai_knowledge.knowledge import KnowledgeRepository, KnowledgeService
from app.modules.ai_knowledge.models import Document, DocumentChunk, IngestionJob
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.repositories import IdempotencyRecordRepository
from app.shared.responses import SuccessResponse


router = APIRouter(tags=["Documents"])
DocumentStatus = Literal[
    "pending", "processing", "ready", "published", "inactive", "failed", "deleted"
]


class DocumentStateChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=2, max_length=500)
    version: int = Field(ge=1)


class DocumentLifecycleError(AppError):
    def __init__(self, code: str, status_code: int = 409, message: str = "文档状态无效") -> None:
        super().__init__(status_code=status_code, code=code, message=message)


@dataclass
class DocumentDependencies:
    session: AsyncSession
    knowledge: KnowledgeService
    artifacts: KnowledgeArtifactStore
    idempotency: IdempotencyService


async def deps() -> AsyncIterator[DocumentDependencies]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield DocumentDependencies(
                session=session,
                knowledge=KnowledgeService(session, KnowledgeRepository(session)),
                artifacts=KnowledgeArtifactStore(
                    settings.knowledge_upload_root,
                    settings.knowledge_max_file_bytes,
                ),
                idempotency=IdempotencyService(
                    session=session,
                    repository=IdempotencyRecordRepository(session),
                ),
            )
    finally:
        await database.dispose()


def _job_data(job: IngestionJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "document_id": job.document_id,
        "stage": job.stage,
        "progress": job.progress,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _document_data(document: Document, latest_job: IngestionJob | None = None) -> dict[str, Any]:
    return {
        "id": document.id,
        "knowledge_base_id": document.knowledge_base_id,
        "title": document.title,
        "original_file_name": document.original_file_name,
        "mime_type": document.mime_type,
        "file_size_bytes": document.file_size_bytes,
        "file_sha256": document.file_sha256,
        "status": document.status,
        "document_version": document.document_version,
        "index_version": document.index_version,
        "page_count": document.page_count,
        "chunk_count": document.chunk_count,
        "published_at": document.published_at,
        "inactive_at": document.inactive_at,
        "expires_at": document.expires_at,
        "latest_job": _job_data(latest_job),
        "created_by": document.created_by,
        "version": document.version,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def _chunk_data(chunk: DocumentChunk) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "document_id": chunk.document_id,
        "document_version": chunk.document_version,
        "chunk_index": chunk.chunk_index,
        "content": chunk.content,
        "source_location": chunk.source_location,
        "page_number": chunk.page_number,
        "token_count": chunk.token_count,
        "clean_status": chunk.clean_status,
        "index_version": chunk.index_version,
    }


def _body(data: object, request_id: str, *, timestamp: datetime | None = None) -> dict[str, Any]:
    return SuccessResponse(
        data=data,
        request_id=request_id,
        timestamp=timestamp or datetime.now(UTC),
    ).model_dump(mode="json")


def _json(status_code: int, body: object) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body)


def _pagination(page: int, page_size: int, total: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _owned_document(
    dependencies: DocumentDependencies,
    user: AuthenticatedUser,
    document_id: UUID,
    *,
    write: bool = False,
    lock: bool = False,
) -> Document:
    statement = select(Document).where(
        Document.id == document_id,
        Document.deleted_at.is_(None),
        Document.status != "deleted",
    )
    if lock:
        statement = statement.with_for_update()
    document = (await dependencies.session.execute(statement)).scalar_one_or_none()
    if document is None:
        raise DocumentLifecycleError("DOCUMENT_NOT_FOUND", 404, "文档不存在")
    await dependencies.knowledge.require(document.knowledge_base_id, user, write)
    return document


async def _latest_jobs(
    session: AsyncSession,
    document_ids: Sequence[UUID],
) -> dict[UUID, IngestionJob]:
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            select(IngestionJob)
            .where(IngestionJob.document_id.in_(document_ids))
            .order_by(
                IngestionJob.document_id,
                IngestionJob.created_at.desc(),
                IngestionJob.id.desc(),
            )
        )
    ).scalars()
    result: dict[UUID, IngestionJob] = {}
    for row in rows:
        result.setdefault(row.document_id, row)
    return result


async def _index_is_complete(session: AsyncSession, document: Document) -> bool:
    if (
        document.index_version is None
        or document.index_version != document.document_version
        or document.chunk_count < 1
    ):
        return False
    count = (
        await session.execute(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_id == document.id,
                DocumentChunk.document_version == document.document_version,
                DocumentChunk.index_version == document.index_version,
                DocumentChunk.clean_status != "excluded",
            )
        )
    ).scalar_one()
    return int(count) == document.chunk_count


@router.get("/api/v1/knowledge-bases/{knowledge_base_id}/documents", operation_id="listDocuments")
async def list_documents(
    knowledge_base_id: UUID,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:read"))],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: DocumentStatus | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> SuccessResponse:
    await dependencies.knowledge.require(knowledge_base_id, user)
    filters = [
        Document.knowledge_base_id == knowledge_base_id,
        Document.deleted_at.is_(None),
        Document.status != "deleted",
    ]
    if status is not None:
        filters.append(Document.status == status)
    normalized_query = (q or "").strip()
    if normalized_query:
        pattern = f"%{_escape_like(normalized_query)}%"
        filters.append(
            or_(
                Document.title.ilike(pattern, escape="\\"),
                Document.original_file_name.ilike(pattern, escape="\\"),
            )
        )
    total = int(
        (await dependencies.session.execute(select(func.count(Document.id)).where(*filters))).scalar_one()
    )
    documents = list(
        (
            await dependencies.session.execute(
                select(Document)
                .where(*filters)
                .order_by(Document.updated_at.desc(), Document.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    jobs = await _latest_jobs(dependencies.session, [item.id for item in documents])
    return SuccessResponse(
        data={
            "items": [_document_data(item, jobs.get(item.id)) for item in documents],
            "pagination": _pagination(page, page_size, total),
        },
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/api/v1/knowledge-bases/{knowledge_base_id}/documents",
    operation_id="uploadDocuments",
    status_code=202,
)
async def upload_documents(
    knowledge_base_id: UUID,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:write"))],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
    files: Annotated[list[UploadFile], File()],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
    expires_at: Annotated[datetime | None, Form()] = None,
) -> JSONResponse:
    if not 1 <= len(files) <= 10:
        raise DocumentLifecycleError("VALIDATION_ERROR", 422, "请求参数校验失败")
    if expires_at is not None and expires_at.tzinfo is None:
        raise DocumentLifecycleError("VALIDATION_ERROR", 422, "expires_at 必须包含时区")

    now = datetime.now(UTC)
    staged: list[tuple[StoredArtifact, UUID, str]] = []
    retained = False
    try:
        async with dependencies.session.begin():
            await dependencies.knowledge.require(knowledge_base_id, user, True)
            for upload in files:
                document_id = uuid4()
                original_name = Path(upload.filename or "upload").name[:255] or "upload"
                suffix = Path(original_name).suffix.lower().lstrip(".")
                artifact = await dependencies.artifacts.save(
                    upload,
                    original_name,
                    content_type=upload.content_type,
                    object_key=(
                        f"ai-knowledge/{knowledge_base_id.hex}/{document_id.hex}/source.{suffix}"
                    ),
                )
                staged.append((artifact, document_id, original_name))

            request_body = {
                "knowledge_base_id": str(knowledge_base_id),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "files": [
                    {
                        "name": name,
                        "sha256": artifact.sha256,
                        "size_bytes": artifact.size_bytes,
                        "mime_type": artifact.mime_type,
                    }
                    for artifact, _, name in staged
                ],
            }
            decision = await dependencies.idempotency.begin(
                user_id=user.user_id,
                endpoint=f"POST /api/v1/knowledge-bases/{knowledge_base_id}/documents",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                return _json(decision.replay.response_status, decision.replay.response_body)
            if decision.pending:
                raise IdempotencyConflict()

            hashes = [artifact.sha256 for artifact, _, _ in staged]
            if len(hashes) != len(set(hashes)):
                raise DocumentLifecycleError("DOCUMENT_ALREADY_EXISTS", 409, "批次包含重复文档")
            duplicate = (
                await dependencies.session.execute(
                    select(Document.id).where(
                        Document.knowledge_base_id == knowledge_base_id,
                        Document.file_sha256.in_(hashes),
                        Document.deleted_at.is_(None),
                        Document.status != "deleted",
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise DocumentLifecycleError("DOCUMENT_ALREADY_EXISTS", 409, "文档已存在")

            items: list[tuple[Document, IngestionJob]] = []
            for artifact, document_id, original_name in staged:
                title = Path(original_name).stem.strip()[:200] or "文档"
                document = Document(
                    id=document_id,
                    knowledge_base_id=knowledge_base_id,
                    title=title,
                    original_file_name=original_name,
                    object_key=artifact.object_key,
                    mime_type=artifact.mime_type,
                    file_size_bytes=artifact.size_bytes,
                    file_sha256=artifact.sha256,
                    status="pending",
                    document_version=1,
                    index_version=None,
                    page_count=None,
                    chunk_count=0,
                    published_at=None,
                    inactive_at=None,
                    expires_at=expires_at.astimezone(UTC) if expires_at else None,
                    created_by=user.user_id,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
                job = IngestionJob(
                    id=uuid4(),
                    document_id=document.id,
                    celery_task_id=None,
                    stage="queued",
                    progress=0,
                    attempt=1,
                    max_attempts=3,
                    error_code=None,
                    error_message=None,
                    started_at=None,
                    finished_at=None,
                    created_by=user.user_id,
                    created_at=now,
                    updated_at=now,
                )
                dependencies.session.add_all((document, job))
                items.append((document, job))
            await dependencies.session.flush()
            body = _body(
                {
                    "items": [
                        {"document": _document_data(document, job), "job": _job_data(job)}
                        for document, job in items
                    ]
                },
                request.state.request_id,
                timestamp=now,
            )
            if not await dependencies.idempotency.complete(
                record_id=decision.record_id,
                response_status=202,
                response_body=body,
                resource_type="document_batch",
                resource_id=str(knowledge_base_id),
            ):
                raise IdempotencyConflict()
            retained = True
        return _json(202, body)
    finally:
        if not retained:
            for artifact, _, _ in staged:
                dependencies.artifacts.delete(artifact.object_key)


@router.get("/api/v1/documents/{document_id}", operation_id="getDocument")
async def get_document(
    document_id: UUID,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:read"))],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
) -> SuccessResponse:
    document = await _owned_document(dependencies, user, document_id)
    jobs = await _latest_jobs(dependencies.session, [document.id])
    return SuccessResponse(
        data=_document_data(document, jobs.get(document.id)),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.delete("/api/v1/documents/{document_id}", operation_id="deleteDocument", status_code=204)
async def delete_document(
    document_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:write"))],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
) -> Response:
    now = datetime.now(UTC)
    async with dependencies.session.begin():
        document = await _owned_document(dependencies, user, document_id, write=True, lock=True)
        document.status = "deleted"
        document.deleted_at = now
        document.updated_at = now
        document.version += 1
        await dependencies.session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.document_id == document.id,
                IngestionJob.stage.not_in(("succeeded", "failed")),
            )
            .values(
                stage="failed",
                error_code="DOCUMENT_DELETED",
                error_message=None,
                finished_at=now,
                updated_at=now,
            )
        )
        # A durable queue row lets the standalone worker clean Chroma and the
        # source artifact after the database has made the document invisible.
        dependencies.session.add(
            IngestionJob(
                id=uuid4(),
                document_id=document.id,
                celery_task_id=f"cleanup:{document.id}",
                stage="queued",
                progress=0,
                attempt=1,
                max_attempts=3,
                error_code=None,
                error_message=None,
                started_at=None,
                finished_at=None,
                created_by=user.user_id,
                created_at=now,
                updated_at=now,
            )
        )
    return Response(status_code=204)


@router.get("/api/v1/documents/{document_id}/chunks", operation_id="listDocumentChunks")
async def list_document_chunks(
    document_id: UUID,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:read"))],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SuccessResponse:
    document = await _owned_document(dependencies, user, document_id)
    if document.status not in {"ready", "published", "inactive"}:
        raise DocumentLifecycleError("DOCUMENT_NOT_READY")
    filters = (
        DocumentChunk.document_id == document.id,
        DocumentChunk.document_version == document.document_version,
    )
    total = int(
        (await dependencies.session.execute(select(func.count(DocumentChunk.id)).where(*filters))).scalar_one()
    )
    chunks = list(
        (
            await dependencies.session.execute(
                select(DocumentChunk)
                .where(*filters)
                .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    return SuccessResponse(
        data={
            "items": [_chunk_data(chunk) for chunk in chunks],
            "pagination": _pagination(page, page_size, total),
        },
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


async def _change_document_state(
    *,
    document_id: UUID,
    target: Literal["published", "inactive"],
    payload: DocumentStateChangeRequest,
    request: Request,
    user: AuthenticatedUser,
    idempotency_key: str,
    dependencies: DocumentDependencies,
) -> JSONResponse:
    endpoint = f"POST /api/v1/documents/{document_id}/{('publish' if target == 'published' else 'deactivate')}"
    now = datetime.now(UTC)
    async with dependencies.session.begin():
        decision = await dependencies.idempotency.begin(
            user_id=user.user_id,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_body=payload.model_dump(mode="json"),
        )
        if decision.replay is not None:
            return _json(decision.replay.response_status, decision.replay.response_body)
        if decision.pending:
            raise IdempotencyConflict()
        document = await _owned_document(dependencies, user, document_id, write=True, lock=True)
        if document.version != payload.version:
            raise DocumentLifecycleError("RESOURCE_VERSION_CONFLICT")
        if target == "published":
            if document.status not in {"ready", "inactive"}:
                raise DocumentLifecycleError("DOCUMENT_NOT_READY")
            if document.expires_at is not None and document.expires_at <= now:
                raise DocumentLifecycleError("DOCUMENT_NOT_READY")
            if not await _index_is_complete(dependencies.session, document):
                raise DocumentLifecycleError("DOCUMENT_INDEX_INCOMPLETE")
            document.status = "published"
            document.published_at = now
            document.inactive_at = None
        else:
            if document.status != "published":
                raise DocumentLifecycleError("DOCUMENT_STATE_CONFLICT")
            document.status = "inactive"
            document.inactive_at = now
        document.version += 1
        document.updated_at = now
        await dependencies.session.flush()
        jobs = await _latest_jobs(dependencies.session, [document.id])
        body = _body(
            _document_data(document, jobs.get(document.id)),
            request.state.request_id,
            timestamp=now,
        )
        if not await dependencies.idempotency.complete(
            record_id=decision.record_id,
            response_status=200,
            response_body=body,
            resource_type="document",
            resource_id=str(document.id),
        ):
            raise IdempotencyConflict()
    return _json(200, body)


@router.post("/api/v1/documents/{document_id}/publish", operation_id="publishDocument")
async def publish_document(
    document_id: UUID,
    payload: DocumentStateChangeRequest,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:publish"))],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
) -> JSONResponse:
    return await _change_document_state(
        document_id=document_id,
        target="published",
        payload=payload,
        request=request,
        user=user,
        idempotency_key=idempotency_key,
        dependencies=dependencies,
    )


@router.post("/api/v1/documents/{document_id}/deactivate", operation_id="deactivateDocument")
async def deactivate_document(
    document_id: UUID,
    payload: DocumentStateChangeRequest,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:publish"))],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
) -> JSONResponse:
    return await _change_document_state(
        document_id=document_id,
        target="inactive",
        payload=payload,
        request=request,
        user=user,
        idempotency_key=idempotency_key,
        dependencies=dependencies,
    )


@router.get("/api/v1/ingestion-jobs/{job_id}", operation_id="getIngestionJob")
async def get_ingestion_job(
    job_id: UUID,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:read"))],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
) -> SuccessResponse:
    job = await dependencies.session.get(IngestionJob, job_id)
    if job is None:
        raise DocumentLifecycleError("INGESTION_JOB_NOT_FOUND", 404, "入库任务不存在")
    await _owned_document(dependencies, user, job.document_id)
    return SuccessResponse(
        data=_job_data(job),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/api/v1/ingestion-jobs/{job_id}/retry",
    operation_id="retryIngestionJob",
    status_code=202,
)
async def retry_ingestion_job(
    job_id: UUID,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("knowledge:write"))],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
    dependencies: Annotated[DocumentDependencies, Depends(deps)],
) -> JSONResponse:
    now = datetime.now(UTC)
    async with dependencies.session.begin():
        job = (
            await dependencies.session.execute(
                select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            raise DocumentLifecycleError("INGESTION_JOB_NOT_FOUND", 404, "入库任务不存在")
        document = await _owned_document(dependencies, user, job.document_id, write=True, lock=True)
        decision = await dependencies.idempotency.begin(
            user_id=user.user_id,
            endpoint=f"POST /api/v1/ingestion-jobs/{job_id}/retry",
            idempotency_key=idempotency_key,
            request_body={},
        )
        if decision.replay is not None:
            return _json(decision.replay.response_status, decision.replay.response_body)
        if decision.pending:
            raise IdempotencyConflict()
        if job.stage != "failed" or job.attempt >= job.max_attempts:
            raise DocumentLifecycleError("INGESTION_RETRY_NOT_ALLOWED")
        job.stage = "queued"
        job.progress = 0
        job.attempt += 1
        job.error_code = None
        job.error_message = None
        job.started_at = None
        job.finished_at = None
        job.updated_at = now
        document.status = "pending"
        document.updated_at = now
        await dependencies.session.flush()
        body = _body(_job_data(job), request.state.request_id, timestamp=now)
        if not await dependencies.idempotency.complete(
            record_id=decision.record_id,
            response_status=202,
            response_body=body,
            resource_type="ingestion_job",
            resource_id=str(job.id),
        ):
            raise IdempotencyConflict()
    return _json(202, body)
