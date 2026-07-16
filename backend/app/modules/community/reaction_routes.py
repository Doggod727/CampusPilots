from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.reactions import ReactionData, ReactionService
from app.modules.community.repositories import ReactionRepository
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/posts", tags=["CommunityInteractions"])


class ReactionType(str, Enum):
    like = "like"
    favorite = "favorite"


class ReactionDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    post_id: UUID
    reaction_type: ReactionType
    active: bool
    like_count: int = Field(ge=0)
    favorite_count: int = Field(ge=0)


ReactionResponse = SuccessResponse[ReactionDataModel]


async def get_reaction_service() -> AsyncIterator[ReactionService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield ReactionService(session=session, repository=ReactionRepository(session))
    finally:
        await database.dispose()


def _model(data: ReactionData) -> ReactionDataModel:
    return ReactionDataModel.model_validate(data, from_attributes=True)


@router.put("/{post_id}/reactions/{reaction_type}", operation_id="putPostReaction", response_model=ReactionResponse)
async def put_reaction(
    post_id: UUID, reaction_type: ReactionType, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[ReactionService, Depends(get_reaction_service)],
) -> ReactionResponse:
    data = await service.put(actor=actor, post_id=post_id, reaction_type=reaction_type.value)
    return SuccessResponse(data=_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))


@router.delete("/{post_id}/reactions/{reaction_type}", operation_id="deletePostReaction", response_model=ReactionResponse)
async def delete_reaction(
    post_id: UUID, reaction_type: ReactionType, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[ReactionService, Depends(get_reaction_service)],
) -> ReactionResponse:
    data = await service.delete(actor=actor, post_id=post_id, reaction_type=reaction_type.value)
    return SuccessResponse(data=_model(data), request_id=request.state.request_id,
                           timestamp=datetime.now(UTC))
