from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.claim_schemas import (
    ClaimCompletionRequest, ClaimContactResponse, ClaimCreateRequest,
    ClaimDecisionRequest, ClaimPageResponse, ClaimResponse, ClaimStatus,
    claim_model, claim_page_model, contact_model,
)
from app.modules.community.claims import LostFoundClaimService
from app.modules.community.encryption import CommunityCipher
from app.modules.community.lost_found import LostFoundQueryService
from app.modules.community.profiles import PlatformPublicUserProfileAdapter
from app.modules.community.repositories import LostFoundClaimRepository, LostFoundRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.repositories import AuditLogRepository, IdempotencyRecordRepository
from app.shared.responses import SuccessResponse

item_router = APIRouter(prefix="/api/v1/lost-found", tags=["LostFound"])
router = APIRouter(prefix="/api/v1/lost-found-claims", tags=["LostFound"])


async def get_claim_service() -> AsyncIterator[LostFoundClaimService]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            profiles = PlatformPublicUserProfileAdapter(session)
            yield LostFoundClaimService(session=session,
                repository=LostFoundClaimRepository(session),
                item_queries=LostFoundQueryService(LostFoundRepository(session), profiles),
                profiles=profiles, cipher=CommunityCipher(settings.community_data_encryption_key),
                idempotency=IdempotencyService(session=session,
                    repository=IdempotencyRecordRepository(session)),
                audit=AuditService(AuditLogRepository(session)))
    finally:
        await database.dispose()


@item_router.post("/{item_id}/claims", operation_id="createLostFoundClaim",
                  status_code=201, response_model=ClaimResponse)
async def create_claim(
    item_id: UUID, payload: ClaimCreateRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundClaimService, Depends(get_claim_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    result = await service.create(actor=actor, target_id=item_id,
        claimant_item_id=payload.claimant_item_id, evidence=payload.evidence,
        idempotency_key=idempotency_key, request_id=request.state.request_id,
        request_body=payload.model_dump(mode="json"))
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("", operation_id="listMyLostFoundClaims", response_model=ClaimPageResponse)
async def list_claims(
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundClaimService, Depends(get_claim_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    role: Literal["claimant", "owner", "all"] = "all",
    status: ClaimStatus | None = None,
) -> ClaimPageResponse:
    data = await service.list(actor=actor, role=role, status=status.value if status else None,
                              page=page, page_size=page_size)
    return SuccessResponse(data=claim_page_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.get("/{claim_id}", operation_id="getLostFoundClaim", response_model=ClaimResponse)
async def get_claim(
    claim_id: UUID, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundClaimService, Depends(get_claim_service)],
) -> ClaimResponse:
    return SuccessResponse(data=claim_model(await service.get(actor=actor, claim_id=claim_id)),
                           request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.post("/{claim_id}/decision", operation_id="decideLostFoundClaim",
             response_model=ClaimResponse)
async def decide_claim(
    claim_id: UUID, payload: ClaimDecisionRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundClaimService, Depends(get_claim_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    result = await service.decide(actor=actor, claim_id=claim_id,
        decision_value=payload.decision, reason=payload.reason, version=payload.version,
        idempotency_key=idempotency_key, request_id=request.state.request_id,
        request_body=body)
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/{claim_id}/contact", operation_id="getLostFoundClaimContact",
            response_model=ClaimContactResponse)
async def get_claim_contact(
    claim_id: UUID, request: Request, response: Response,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundClaimService, Depends(get_claim_service)],
) -> ClaimContactResponse:
    data = await service.contact(actor=actor, claim_id=claim_id, request_id=request.state.request_id)
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(data=contact_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.post("/{claim_id}/completion", operation_id="confirmLostFoundClaimCompletion",
             response_model=ClaimResponse)
async def complete_claim(
    claim_id: UUID, payload: ClaimCompletionRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[LostFoundClaimService, Depends(get_claim_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    result = await service.complete(actor=actor, claim_id=claim_id, version=payload.version,
        idempotency_key=idempotency_key, request_id=request.state.request_id,
        request_body=body)
    return JSONResponse(status_code=result.status_code, content=result.body)
