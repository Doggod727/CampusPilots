from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.repositories import (
    AuditLogRepository,
    RbacWriteRepository,
    RoleListItem,
)


class RoleNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="ROLE_NOT_FOUND", message="角色不存在")


class PermissionNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="PERMISSION_NOT_FOUND",
            message="权限不存在",
        )


class ResourceVersionConflict(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="RESOURCE_VERSION_CONFLICT",
            message="数据已被其他操作更新，请刷新后重试",
        )


class RolePermissionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: RbacWriteRepository,
        audit_service: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit_service = audit_service
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def replace_permissions(
        self,
        *,
        actor: AuthenticatedUser,
        role_id: UUID,
        expected_version: int,
        permission_ids: list[UUID],
        request_id: str,
    ) -> RoleListItem:
        async with self._session.begin():
            current = await self._repository.get_role(role_id)
            if current is None:
                raise RoleNotFound()
            if current.role.version != expected_version:
                raise ResourceVersionConflict()
            permissions = await self._repository.get_permissions_by_ids(permission_ids)
            if len(permissions) != len(permission_ids):
                raise PermissionNotFound()
            now = self._current_time()
            before_ids = [str(permission.id) for permission in current.permissions]
            if not await self._repository.update_role_if_version(
                role_id, expected_version, {}, now
            ):
                raise ResourceVersionConflict()
            await self._repository.clear_role_permissions(role_id)
            self._repository.add_role_permissions(
                role_id,
                [permission.id for permission in permissions],
            )
            current.role.version = expected_version + 1
            current.role.updated_at = now
            self._audit_service.record_success(
                action="role.permissions.replace",
                resource_type="role",
                resource_id=str(role_id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                before_data={
                    "role_id": str(role_id),
                    "version": expected_version,
                    "permission_ids": before_ids,
                },
                after_data={
                    "role_id": str(role_id),
                    "version": current.role.version,
                    "permission_ids": [str(permission.id) for permission in permissions],
                },
            )
            return RoleListItem(
                role=current.role,
                permissions=tuple(permissions),
                user_count=current.user_count,
            )

    def _current_time(self) -> datetime:
        now = self._now()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


@asynccontextmanager
async def role_permission_service_context(
    settings: Settings,
) -> AsyncIterator[RolePermissionService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield RolePermissionService(
                session=session,
                repository=RbacWriteRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()
