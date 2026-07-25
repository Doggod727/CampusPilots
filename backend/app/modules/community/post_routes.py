from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.post_schemas import (
    PostCreateRequest,
    PostPageResponse,
    PostResponse,
    PostSort,
    PostUpdateRequest,
    post_model,
    post_page_model,
)
from app.modules.community.posts import PostQueryService, PostService
from app.modules.community.profiles import PlatformPublicUserProfileAdapter
from app.modules.community.repositories import PostRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import SensitiveWordScanner
from app.modules.platform.repositories import (
    AuditLogRepository,
    IdempotencyRecordRepository,
    ModerationCaseRepository,
    SensitiveWordRepository,
)
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/posts", tags=["Posts"])


async def get_post_service() -> AsyncIterator[PostService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            repository = PostRepository(session)
            audit = AuditService(AuditLogRepository(session))
            queries = PostQueryService(
                repository, PlatformPublicUserProfileAdapter(session),
            )
            yield PostService(
                session=session,
                repository=repository,
                queries=queries,
                moderation=ModerationService(
                    session=session,
                    scanner=SensitiveWordScanner(SensitiveWordRepository(session)),
                    repository=ModerationCaseRepository(session),
                    audit_service=audit,
                ),
                idempotency=IdempotencyService(
                    session=session,
                    repository=IdempotencyRecordRepository(session),
                ),
                audit=audit,
            )
    finally:
        await database.dispose()


async def get_post_query_service() -> AsyncIterator[PostQueryService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield PostQueryService(
                PostRepository(session), PlatformPublicUserProfileAdapter(session),
            )
    finally:
        await database.dispose()


@router.get("", operation_id="listPosts", response_model=PostPageResponse)
async def list_posts(
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[PostQueryService, Depends(get_post_query_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    topic_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    mine: bool = False,
    sort: PostSort = PostSort.newest,
) -> PostPageResponse:
    result = await service.list(
        actor=actor, page=page, page_size=page_size, topic_id=topic_id,
        q=q, mine=mine, sort=sort.value,
    )
    return SuccessResponse(
        data=post_page_model(result), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post("", operation_id="createPost", status_code=201, response_model=PostResponse)
async def create_post(
    payload: PostCreateRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[PostService, Depends(get_post_service)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
) -> JSONResponse:
    request_body = payload.model_dump(mode="json")
    result = await service.create(
        actor=actor, idempotency_key=idempotency_key,
        request_id=request.state.request_id, request_body=request_body,
        **payload.model_dump(mode="python"),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/{post_id}", operation_id="getPost", response_model=PostResponse)
async def get_post(
    post_id: UUID,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[PostQueryService, Depends(get_post_query_service)],
) -> PostResponse:
    return SuccessResponse(
        data=post_model(await service.get(actor=actor, post_id=post_id)),
        request_id=request.state.request_id, timestamp=datetime.now(UTC),
    )


@router.patch("/{post_id}", operation_id="updatePost", response_model=PostResponse)
async def update_post(
    post_id: UUID,
    payload: PostUpdateRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[PostService, Depends(get_post_service)],
) -> PostResponse:
    item = await service.update(
        actor=actor, post_id=post_id, version=payload.version,
        changes=payload.model_dump(exclude_unset=True, exclude={"version"}, mode="python"),
        request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=post_model(item), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.delete("/{post_id}", operation_id="deletePost", status_code=204)
async def delete_post(
    post_id: UUID,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[PostService, Depends(get_post_service)],
) -> Response:
    await service.delete(
        actor=actor, post_id=post_id, request_id=request.state.request_id,
    )
    return Response(status_code=204)
