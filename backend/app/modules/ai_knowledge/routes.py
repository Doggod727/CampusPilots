from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.ai_knowledge.knowledge import KnowledgeRepository, KnowledgeService
from app.modules.ai_knowledge.knowledge_schemas import (
    KnowledgeBaseCreateRequest,
    KnowledgeBasePageResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
    KnowledgeBaseVisibility,
    knowledge_base_model,
    knowledge_base_page_model,
)
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.repositories import IdempotencyRecordRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["KnowledgeBases"])


async def get_knowledge_service() -> AsyncIterator[KnowledgeService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            repository = KnowledgeRepository(session)
            yield KnowledgeService(
                session,
                repository,
                IdempotencyService(
                    session=session,
                    repository=IdempotencyRecordRepository(session),
                ),
            )
    finally:
        await database.dispose()


# Kept as a compatibility alias for tests and integrations built on the first M1 cut.
service_dep = get_knowledge_service


@router.get(
    "",
    operation_id="listKnowledgeBases",
    response_model=KnowledgeBasePageResponse,
)
async def list_knowledge_bases(
    request: Request,
    actor: Annotated[
        AuthenticatedUser, Depends(require_permissions("knowledge:read"))
    ],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=100)] = None,
    visibility: KnowledgeBaseVisibility | None = None,
) -> KnowledgeBasePageResponse:
    result = await service.list(
        user=actor,
        page=page,
        page_size=page_size,
        query=q,
        visibility=visibility,
    )
    return SuccessResponse(
        data=knowledge_base_page_model(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "",
    operation_id="createKnowledgeBase",
    status_code=201,
    response_model=KnowledgeBaseResponse,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    request: Request,
    actor: Annotated[
        AuthenticatedUser, Depends(require_permissions("knowledge:write"))
    ],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> JSONResponse:
    request_body = payload.model_dump(mode="json")
    member_user_ids = payload.member_user_ids
    values = payload.model_dump(
        mode="python", exclude={"member_user_ids"}
    )
    async with service.s.begin():
        result = await service.create(
            actor,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            request_body=request_body,
            member_user_ids=member_user_ids,
            **values,
        )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get(
    "/{knowledge_base_id}",
    operation_id="getKnowledgeBase",
    response_model=KnowledgeBaseResponse,
)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    request: Request,
    actor: Annotated[
        AuthenticatedUser, Depends(require_permissions("knowledge:read"))
    ],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeBaseResponse:
    item = await service.get_data(knowledge_base_id, actor)
    return SuccessResponse(
        data=knowledge_base_model(item),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.patch(
    "/{knowledge_base_id}",
    operation_id="updateKnowledgeBase",
    response_model=KnowledgeBaseResponse,
)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeBaseUpdateRequest,
    request: Request,
    actor: Annotated[
        AuthenticatedUser, Depends(require_permissions("knowledge:write"))
    ],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ] = None,
) -> JSONResponse:
    request_body = payload.model_dump(mode="json")
    members = payload.member_user_ids if "member_user_ids" in payload.model_fields_set else None
    changes = payload.model_dump(
        mode="python",
        exclude_unset=True,
        exclude={"version", "member_user_ids"},
    )
    async with service.s.begin():
        result = await service.update(
            knowledge_base_id,
            actor,
            payload.version,
            changes,
            member_user_ids=members,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            request_body=request_body,
        )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.delete(
    "/{knowledge_base_id}",
    operation_id="deleteKnowledgeBase",
    status_code=204,
)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    actor: Annotated[
        AuthenticatedUser, Depends(require_permissions("knowledge:write"))
    ],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> Response:
    async with service.s.begin():
        await service.delete(knowledge_base_id, actor)
    return Response(status_code=204)
