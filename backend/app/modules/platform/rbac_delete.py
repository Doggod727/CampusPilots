from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.repositories import AuditLogRepository, RbacWriteRepository


class SystemRoleProtected(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="SYSTEM_ROLE_PROTECTED",
            message="系统角色不可删除",
        )


class RoleInUse(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="ROLE_IN_USE",
            message="角色仍被用户使用",
        )


class RoleDeleteService:
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

    async def delete_role(
        self,
        *,
        actor: AuthenticatedUser,
        role_id: UUID,
        request_id: str,
    ) -> None:
        async with self._session.begin():
            role = await self._repository.get_role_for_update(role_id)
            if role is None:
                from app.modules.platform.rbac_update import RoleNotFound

                raise RoleNotFound()
            if role.is_system:
                raise SystemRoleProtected()
            if await self._repository.count_role_assignments(role_id):
                raise RoleInUse()
            try:
                if not await self._repository.delete_role(role_id):
                    from app.modules.platform.rbac_update import RoleNotFound

                    raise RoleNotFound()
            except IntegrityError:
                raise RoleInUse() from None
            self._audit_service.record_success(
                action="role.delete",
                resource_type="role",
                resource_id=str(role.id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                before_data={
                    "id": str(role.id),
                    "code": role.code,
                    "name": role.name,
                    "is_system": role.is_system,
                },
                after_data={"status": "deleted"},
            )

    def _current_time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@asynccontextmanager
async def role_delete_service_context(
    settings: Settings,
) -> AsyncIterator[RoleDeleteService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield RoleDeleteService(
                session=session,
                repository=RbacWriteRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()
