from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.anonymous_identity import (
    AnonymousIdentityData, AnonymousIdentityRepository, AnonymousIdentityService,
    PlatformHistoricalIdentityAdapter,
)
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.repositories import AuditLogRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/community/anonymous-identities",
                   tags=["CommunityInteractions"])


class AnonymousTargetType(str, Enum):
    post = "post"
    comment = "comment"


class RevealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: AnonymousTargetType
    target_id: UUID
    reason: str = Field(min_length=2, max_length=500)


class RevealDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: AnonymousTargetType
    target_id: UUID
    author_user_id: UUID
    username: str
    display_name: str
    reason: str
    revealed_at: datetime


RevealResponse = SuccessResponse[RevealDataModel]


async def get_anonymous_identity_service() -> AsyncIterator[AnonymousIdentityService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield AnonymousIdentityService(
                session=session, repository=AnonymousIdentityRepository(session),
                identities=PlatformHistoricalIdentityAdapter(session),
                audit=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()


@router.post("/reveal", operation_id="revealAnonymousIdentity", response_model=RevealResponse)
async def reveal_anonymous_identity(
    payload: RevealRequest, request: Request, response: Response,
    actor: Annotated[AuthenticatedUser, Depends(
        require_permissions("community:anonymous_identity:read"))],
    service: Annotated[AnonymousIdentityService, Depends(get_anonymous_identity_service)],
) -> RevealResponse:
    data = await service.reveal(
        actor=actor, target_type=payload.target_type.value,
        target_id=payload.target_id, reason=payload.reason,
        request_id=request.state.request_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(
        data=RevealDataModel.model_validate(data, from_attributes=True),
        request_id=request.state.request_id, timestamp=datetime.now(UTC),
    )
