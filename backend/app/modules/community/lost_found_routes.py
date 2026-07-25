from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.encryption import CommunityCipher
from app.modules.community.lost_found import LostFoundQueryService, LostFoundService
from app.modules.community.lost_found_schemas import (
    LostFoundCreateRequest, LostFoundItemResponse, LostFoundItemType,
    LostFoundMatchPageResponse, LostFoundPageResponse, LostFoundUpdateRequest,
    lost_found_model, lost_found_page_model, match_page_model,
)
from app.modules.community.matcher import LostFoundMatcherService
from app.modules.community.profiles import PlatformPublicUserProfileAdapter
from app.modules.community.repositories import LostFoundRepository
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

router = APIRouter(prefix="/api/v1/lost-found", tags=["LostFound"])


async def get_lost_found_query_service() -> AsyncIterator[LostFoundQueryService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield LostFoundQueryService(LostFoundRepository(session), PlatformPublicUserProfileAdapter(session))
    finally:
        await database.dispose()


async def get_lost_found_service() -> AsyncIterator[LostFoundService]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            repository = LostFoundRepository(session)
            queries = LostFoundQueryService(repository, PlatformPublicUserProfileAdapter(session))
            audit = AuditService(AuditLogRepository(session))
            matcher = LostFoundMatcherService(session=session, repository=repository, queries=queries,
                algorithm_version=settings.community_match_algorithm_version)
            yield LostFoundService(session=session, repository=repository, queries=queries,
                cipher=CommunityCipher(settings.community_data_encryption_key),
                moderation=ModerationService(session=session,
                    scanner=SensitiveWordScanner(SensitiveWordRepository(session)),
                    repository=ModerationCaseRepository(session), audit_service=audit),
                idempotency=IdempotencyService(session=session,
                    repository=IdempotencyRecordRepository(session)), audit=audit, matcher=matcher)
    finally:
        await database.dispose()


async def get_matcher_service() -> AsyncIterator[LostFoundMatcherService]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            repository = LostFoundRepository(session)
            yield LostFoundMatcherService(session=session, repository=repository,
                queries=LostFoundQueryService(repository, PlatformPublicUserProfileAdapter(session)),
                algorithm_version=settings.community_match_algorithm_version)
    finally:
        await database.dispose()


@router.get("", operation_id="listLostFoundItems", response_model=LostFoundPageResponse)
async def list_lost_found(
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[LostFoundQueryService, Depends(get_lost_found_query_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    item_type: LostFoundItemType | None = None,
    category: Annotated[str | None, Query(max_length=50)] = None,
    location: Annotated[str | None, Query(max_length=100)] = None,
    occurred_from: datetime | None = None, occurred_to: datetime | None = None,
    mine: bool = False,
) -> LostFoundPageResponse:
    data = await service.list(actor=actor, page=page, page_size=page_size,
        item_type=item_type.value if item_type else None, category=category, location=location,
        occurred_from=occurred_from, occurred_to=occurred_to, mine=mine)
    return SuccessResponse(data=lost_found_page_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.post("", operation_id="createLostFoundItem", status_code=201,
             response_model=LostFoundItemResponse)
async def create_lost_found(
    payload: LostFoundCreateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundService, Depends(get_lost_found_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    values = payload.model_dump(mode="python")
    values["item_type"] = payload.item_type.value
    values["contact_type"] = payload.contact_type.value
    result = await service.create(actor=actor, idempotency_key=idempotency_key,
        request_id=request.state.request_id, request_body=payload.model_dump(mode="json"), **values)
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/{item_id}", operation_id="getLostFoundItem", response_model=LostFoundItemResponse)
async def get_lost_found(
    item_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[LostFoundQueryService, Depends(get_lost_found_query_service)],
) -> LostFoundItemResponse:
    return SuccessResponse(data=lost_found_model(await service.get(actor=actor, item_id=item_id)),
                           request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.patch("/{item_id}", operation_id="updateLostFoundItem", response_model=LostFoundItemResponse)
async def update_lost_found(
    item_id: UUID, payload: LostFoundUpdateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundService, Depends(get_lost_found_service)],
) -> LostFoundItemResponse:
    changes = payload.model_dump(exclude_unset=True, exclude={"version"}, mode="python")
    if payload.contact_type is not None:
        changes["contact_type"] = payload.contact_type.value
    data = await service.update(actor=actor, item_id=item_id, version=payload.version,
                                changes=changes, request_id=request.state.request_id)
    return SuccessResponse(data=lost_found_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.delete("/{item_id}", operation_id="deleteLostFoundItem", status_code=204)
async def delete_lost_found(
    item_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundService, Depends(get_lost_found_service)],
) -> Response:
    await service.delete(actor=actor, item_id=item_id, request_id=request.state.request_id)
    return Response(status_code=204)


@router.get("/{item_id}/matches", operation_id="listLostFoundMatches",
            response_model=LostFoundMatchPageResponse)
async def list_lost_found_matches(
    item_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundMatcherService, Depends(get_matcher_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LostFoundMatchPageResponse:
    data = await service.list(actor=actor, item_id=item_id, page=page, page_size=page_size)
    return SuccessResponse(data=match_page_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))
