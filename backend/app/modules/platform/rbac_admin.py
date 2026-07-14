from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.models import Role
from app.modules.platform.repositories import (
    AuditLogRepository,
    RbacWriteRepository,
    RoleListItem,
)


class DuplicateRole(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="DUPLICATE_RESOURCE",
            message="角色资源已存在",
        )


class PermissionNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="PERMISSION_NOT_FOUND",
            message="权限不存在",
        )


class RoleAdminService:
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

    async def create_role(
        self,
        *,
        actor: AuthenticatedUser,
        code: str,
        name: str,
        description: str | None,
        permission_ids: list[UUID],
        request_id: str,
    ) -> RoleListItem:
        async with self._session.begin():
            if await self._repository.get_role_by_code(code) is not None:
                raise DuplicateRole()
            permissions = await self._repository.get_permissions_by_ids(permission_ids)
            if len(permissions) != len(permission_ids):
                raise PermissionNotFound()

            now = self._current_time()
            role = Role(
                id=uuid4(),
                code=code,
                name=name,
                description=description,
                is_system=False,
                version=1,
                created_at=now,
                updated_at=now,
            )
            self._repository.add_role(role)
            try:
                await self._session.flush()
            except IntegrityError:
                raise DuplicateRole() from None
            self._repository.add_role_permissions(
                role.id,
                [permission.id for permission in permissions],
            )
            self._audit_service.record_success(
                action="role.create",
                resource_type="role",
                resource_id=str(role.id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                after_data={
                    "id": str(role.id),
                    "code": role.code,
                    "name": role.name,
                    "is_system": False,
                    "permission_ids": [str(permission.id) for permission in permissions],
                },
            )
            return RoleListItem(role=role, permissions=tuple(permissions), user_count=0)

    def _current_time(self) -> datetime:
        now = self._now()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


@asynccontextmanager
async def role_admin_service_context(
    settings: Settings,
) -> AsyncIterator[RoleAdminService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield RoleAdminService(
                session=session,
                repository=RbacWriteRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()
