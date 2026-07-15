from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.platform.repositories import RbacRepository, UserRepository


class InternalServiceUnauthorized(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=401, code="AUTH_UNAUTHORIZED", message="内部服务身份无效")


class InternalServiceNotConfigured(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=503, code="INTERNAL_SERVICE_NOT_CONFIGURED", message="内部服务身份尚未配置")


class InternalServicePrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    service: str = "agent_runtime"


def verify_internal_service_token(authorization: str | None, settings: Settings) -> InternalServicePrincipal:
    configured=settings.internal_tool_secret
    if configured is None:
        raise InternalServiceNotConfigured()
    if not authorization or not authorization.startswith("Bearer "):
        raise InternalServiceUnauthorized()
    supplied=authorization[7:]
    if not supplied or not secrets.compare_digest(supplied,configured.get_secret_value()):
        raise InternalServiceUnauthorized()
    return InternalServicePrincipal()


async def get_internal_service_principal(
    authorization: Annotated[str | None, Header(alias="Authorization")]=None,
    settings: Settings=Depends(get_settings),
) -> InternalServicePrincipal:
    return verify_internal_service_token(authorization,settings)


class InternalUserContextLoader:
    def __init__(self, users: UserRepository, rbac: RbacRepository) -> None:
        self._users=users;self._rbac=rbac

    async def load(self,user_id:UUID,request_id:str) -> UserContext:
        user=await self._users.get_by_id(user_id)
        if user is None or user.status!="active":
            raise InternalServiceUnauthorized()
        roles=await self._rbac.list_roles_for_user(user_id)
        permissions=await self._rbac.list_permission_codes_for_user(user_id)
        return UserContext(user_id=user.id,username=user.username,roles=tuple(role.code for role in roles),permissions=tuple(permissions),request_id=request_id)
