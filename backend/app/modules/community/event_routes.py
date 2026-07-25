from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.event_schemas import (
    EventCancelRequest, EventCreateRequest, EventPageResponse, EventResponse,
    EventRegistrationPageResponse, EventRegistrationResponse, EventUpdateRequest,
    event_model, event_page_model, registration_model, registration_page_model,
)
from app.modules.community.events import EventQueryService, EventService
from app.modules.community.profiles import PlatformPublicUserProfileAdapter
from app.modules.community.repositories import EventRepository
from app.modules.community.registrations import EventRegistrationService
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

router = APIRouter(prefix="/api/v1/events", tags=["CampusEvents"])


async def get_event_query_service() -> AsyncIterator[EventQueryService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield EventQueryService(EventRepository(session), PlatformPublicUserProfileAdapter(session))
    finally:
        await database.dispose()


async def get_event_service() -> AsyncIterator[EventService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            repository = EventRepository(session)
            audit = AuditService(AuditLogRepository(session))
            queries = EventQueryService(repository, PlatformPublicUserProfileAdapter(session))
            yield EventService(
                session=session, repository=repository, queries=queries,
                moderation=ModerationService(
                    session=session, scanner=SensitiveWordScanner(SensitiveWordRepository(session)),
                    repository=ModerationCaseRepository(session), audit_service=audit,
                ),
                idempotency=IdempotencyService(session=session, repository=IdempotencyRecordRepository(session)),
                audit=audit,
            )
    finally:
        await database.dispose()


async def get_registration_service() -> AsyncIterator[EventRegistrationService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield EventRegistrationService(
                session=session, repository=EventRepository(session),
                profiles=PlatformPublicUserProfileAdapter(session),
                idempotency=IdempotencyService(session=session,
                    repository=IdempotencyRecordRepository(session)),
                audit=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()


@router.get("", operation_id="listCampusEvents", response_model=EventPageResponse)
async def list_events(
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[EventQueryService, Depends(get_event_query_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    category: Annotated[str | None, Query(max_length=50)] = None,
    starts_from: datetime | None = None, starts_to: datetime | None = None,
    available_only: bool = False, mine: bool = False,
) -> EventPageResponse:
    data = await service.list(actor=actor, page=page, page_size=page_size, category=category,
                              starts_from=starts_from, starts_to=starts_to,
                              available_only=available_only, mine=mine)
    return SuccessResponse(data=event_page_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.post("", operation_id="createCampusEvent", status_code=201, response_model=EventResponse)
async def create_event(
    payload: EventCreateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[EventService, Depends(get_event_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    result = await service.create(actor=actor, idempotency_key=idempotency_key,
                                  request_id=request.state.request_id, request_body=body,
                                  **payload.model_dump(mode="python"))
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/{event_id}", operation_id="getCampusEvent", response_model=EventResponse)
async def get_event(
    event_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:read"))],
    service: Annotated[EventQueryService, Depends(get_event_query_service)],
) -> EventResponse:
    return SuccessResponse(data=event_model(await service.get(actor=actor, event_id=event_id)),
                           request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.patch("/{event_id}", operation_id="updateCampusEvent", response_model=EventResponse)
async def update_event(
    event_id: UUID, payload: EventUpdateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[EventService, Depends(get_event_service)],
) -> EventResponse:
    item = await service.update(actor=actor, event_id=event_id, version=payload.version,
        changes=payload.model_dump(exclude_unset=True, exclude={"version"}, mode="python"),
        request_id=request.state.request_id)
    return SuccessResponse(data=event_model(item), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.post("/{event_id}/cancel", operation_id="cancelCampusEvent", response_model=EventResponse)
async def cancel_event(
    event_id: UUID, payload: EventCancelRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[EventService, Depends(get_event_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    result = await service.cancel(actor=actor, event_id=event_id, version=payload.version,
        reason=payload.reason, idempotency_key=idempotency_key, request_id=request.state.request_id,
        request_body=body)
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/{event_id}/registrations", operation_id="listEventRegistrations",
            response_model=EventRegistrationPageResponse)
async def list_event_registrations(
    event_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[EventRegistrationService, Depends(get_registration_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EventRegistrationPageResponse:
    data = await service.list(actor=actor, event_id=event_id, page=page, page_size=page_size)
    return SuccessResponse(data=registration_page_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.post("/{event_id}/registrations", operation_id="registerCampusEvent",
             response_model=EventRegistrationResponse)
async def register_event(
    event_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[EventRegistrationService, Depends(get_registration_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    result = await service.register(actor=actor, event_id=event_id,
                                    idempotency_key=idempotency_key,
                                    request_id=request.state.request_id)
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.delete("/{event_id}/registrations/me", operation_id="cancelMyEventRegistration",
               response_model=EventRegistrationResponse)
async def cancel_my_event_registration(
    event_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[EventRegistrationService, Depends(get_registration_service)],
) -> EventRegistrationResponse:
    data = await service.cancel(actor=actor, event_id=event_id, request_id=request.state.request_id)
    return SuccessResponse(data=registration_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))
