from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.comment_schemas import (
    CommentCreateRequest, CommentPageResponse, CommentResponse, CommentUpdateRequest,
    comment_model, comment_page_model,
)
from app.modules.community.comments import CommentService
from app.modules.community.profiles import PlatformPublicUserProfileAdapter
from app.modules.community.repositories import CommentRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import SensitiveWordScanner
from app.modules.platform.repositories import (
    AuditLogRepository, IdempotencyRecordRepository, ModerationCaseRepository,
    SensitiveWordRepository,
)
from app.shared.responses import SuccessResponse

router = APIRouter(tags=["Posts"])


async def get_comment_service() -> AsyncIterator[CommentService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            audit = AuditService(AuditLogRepository(session))
            yield CommentService(
                session=session, repository=CommentRepository(session),
                profiles=PlatformPublicUserProfileAdapter(session),
                moderation=ModerationService(
                    session=session,
                    scanner=SensitiveWordScanner(SensitiveWordRepository(session)),
                    repository=ModerationCaseRepository(session), audit_service=audit,
                ),
                idempotency=IdempotencyService(
                    session=session, repository=IdempotencyRecordRepository(session),
                ), audit=audit,
            )
    finally:
        await database.dispose()


@router.get("/api/v1/posts/{post_id}/comments", operation_id="listPostComments", response_model=CommentPageResponse)
async def list_comments(
    post_id: UUID, request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[CommentService, Depends(get_comment_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CommentPageResponse:
    result = await service.list(post_id=post_id, page=page, page_size=page_size)
    return SuccessResponse(data=comment_page_model(result), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.post("/api/v1/posts/{post_id}/comments", operation_id="createComment", status_code=201, response_model=CommentResponse)
async def create_comment(
    post_id: UUID, payload: CommentCreateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[CommentService, Depends(get_comment_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    result = await service.create(
        actor=actor, post_id=post_id, idempotency_key=idempotency_key,
        request_id=request.state.request_id, request_body=body,
        **payload.model_dump(mode="python"),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.patch("/api/v1/comments/{comment_id}", operation_id="updateComment", response_model=CommentResponse)
async def update_comment(
    comment_id: UUID, payload: CommentUpdateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[CommentService, Depends(get_comment_service)],
) -> CommentResponse:
    item = await service.update(
        actor=actor, comment_id=comment_id, content_markdown=payload.content_markdown,
        version=payload.version, request_id=request.state.request_id,
    )
    return SuccessResponse(data=comment_model(item), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.delete("/api/v1/comments/{comment_id}", operation_id="deleteComment", status_code=204)
async def delete_comment(
    comment_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[CommentService, Depends(get_comment_service)],
) -> Response:
    await service.delete(actor=actor, comment_id=comment_id, request_id=request.state.request_id)
    return Response(status_code=204)
