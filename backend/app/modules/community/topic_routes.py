from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.repositories import TopicRepository
from app.modules.community.topic_schemas import (
    TopicCreateRequest,
    TopicPageResponse,
    TopicResponse,
    TopicStatus,
    TopicUpdateRequest,
    topic_model,
    topic_page_model,
)
from app.modules.community.topics import TopicService
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.repositories import AuditLogRepository, IdempotencyRecordRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/topics", tags=["Topics"])


async def get_topic_service() -> AsyncIterator[TopicService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield TopicService(
                session=session, repository=TopicRepository(session),
                idempotency=IdempotencyService(
                    session=session, repository=IdempotencyRecordRepository(session)
                ),
                audit=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()


@router.get("", operation_id="listTopics", response_model=TopicPageResponse)
async def list_topics(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[TopicService, Depends(get_topic_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: TopicStatus = TopicStatus.active,
) -> TopicPageResponse:
    result = await service.list(page=page, page_size=page_size, status=status.value)
    return SuccessResponse(
        data=topic_page_model(result), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post("", operation_id="createTopic", status_code=201, response_model=TopicResponse)
async def create_topic(
    payload: TopicCreateRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:moderate"))],
    service: Annotated[TopicService, Depends(get_topic_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    request_body = payload.model_dump(mode="json")
    result = await service.create(
        actor=actor, idempotency_key=idempotency_key,
        request_id=request.state.request_id, request_body=request_body, **request_body,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/{topic_id}", operation_id="getTopic", response_model=TopicResponse)
async def get_topic(
    topic_id: UUID, request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[TopicService, Depends(get_topic_service)],
) -> TopicResponse:
    return SuccessResponse(
        data=topic_model(await service.get(topic_id)),
        request_id=request.state.request_id, timestamp=datetime.now(UTC),
    )


@router.patch("/{topic_id}", operation_id="updateTopic", response_model=TopicResponse)
async def update_topic(
    topic_id: UUID, payload: TopicUpdateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:moderate"))],
    service: Annotated[TopicService, Depends(get_topic_service)],
) -> TopicResponse:
    changes = payload.model_dump(exclude_unset=True, exclude={"version"}, mode="python")
    item = await service.update(
        actor=actor, topic_id=topic_id, version=payload.version,
        changes=changes, request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=topic_model(item), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.delete("/{topic_id}", operation_id="deleteTopic", status_code=204)
async def delete_topic(
    topic_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:moderate"))],
    service: Annotated[TopicService, Depends(get_topic_service)],
) -> Response:
    await service.delete(actor=actor, topic_id=topic_id, request_id=request.state.request_id)
    return Response(status_code=204)
