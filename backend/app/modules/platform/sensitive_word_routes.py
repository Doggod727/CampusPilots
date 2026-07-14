from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.repositories import SensitiveWordRepository
from app.modules.platform.sensitive_word_schemas import (
    SensitiveScope,
    SensitiveWordCreateRequest,
    SensitiveWordPageData,
    SensitiveWordPageResponse,
    SensitiveWordResponse,
    sensitive_word_data,
)
from app.modules.platform.sensitive_words import SensitiveWordService, sensitive_word_service_context
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/sensitive-words", tags=["SensitiveWords"])


async def get_repository() -> AsyncIterator[SensitiveWordRepository]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield SensitiveWordRepository(session)
    finally:
        await database.dispose()


async def get_service() -> AsyncIterator[SensitiveWordService]:
    async with sensitive_word_service_context(get_settings()) as service:
        yield service


@router.get("", operation_id="listSensitiveWords", response_model=SensitiveWordPageResponse)
async def list_sensitive_words(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("sensitive_word:read"))],
    repository: Annotated[SensitiveWordRepository, Depends(get_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=100)] = None,
    scope: SensitiveScope | None = None,
    enabled: bool | None = None,
) -> SensitiveWordPageResponse:
    items, total = await repository.list_page(
        page=page, page_size=page_size, query=q, scope=scope, enabled=enabled
    )
    return SuccessResponse(
        data=SensitiveWordPageData(
            items=[sensitive_word_data(item) for item in items],
            pagination={
                "page": page, "page_size": page_size, "total": total,
                "total_pages": ceil(total / page_size) if total else 0,
            },
        ), request_id=request.state.request_id, timestamp=datetime.now(UTC),
    )


@router.post("", operation_id="createSensitiveWord", status_code=201, response_model=SensitiveWordResponse)
async def create_sensitive_word(
    payload: SensitiveWordCreateRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("sensitive_word:write"))],
    service: Annotated[SensitiveWordService, Depends(get_service)],
) -> SensitiveWordResponse:
    rule = await service.create(
        actor=actor, word=payload.word, match_type=payload.match_type,
        action=payload.action, replacement=payload.replacement, scope=payload.scope,
        enabled=payload.enabled, request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=sensitive_word_data(rule), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.delete("/{word_id}", operation_id="deleteSensitiveWord", response_model=SuccessResponse[dict[str, object]])
async def delete_sensitive_word(
    word_id: UUID,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("sensitive_word:write"))],
    service: Annotated[SensitiveWordService, Depends(get_service)],
) -> SuccessResponse[dict[str, object]]:
    await service.delete(actor=actor, word_id=word_id, request_id=request.state.request_id)
    return SuccessResponse(
        data={}, request_id=request.state.request_id, timestamp=datetime.now(UTC)
    )
