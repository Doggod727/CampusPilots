from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.config_schemas import (
    ConfigListData,
    ConfigListResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    config_data,
)
from app.modules.platform.config_service import ConfigService, config_service_context
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/configs", tags=["Config"])


async def get_service() -> AsyncIterator[ConfigService]:
    async with config_service_context(get_settings()) as service:
        yield service


@router.get("", operation_id="listConfigs", response_model=ConfigListResponse)
async def list_configs(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("config:read"))],
    service: Annotated[ConfigService, Depends(get_service)],
    namespace: Annotated[str | None, Query(max_length=50)] = None,
) -> ConfigListResponse:
    configs = await service.list(namespace)
    return SuccessResponse(
        data=ConfigListData(items=[config_data(item) for item in configs]),
        request_id=request.state.request_id, timestamp=datetime.now(UTC),
    )


@router.patch("/{config_key}", operation_id="updateConfig", response_model=ConfigResponse)
async def update_config(
    config_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")],
    payload: ConfigUpdateRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("config:write"))],
    service: Annotated[ConfigService, Depends(get_service)],
) -> ConfigResponse:
    config = await service.update(
        actor=actor, key=config_key, value=payload.value,
        expected_version=payload.version, request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=config_data(config), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
