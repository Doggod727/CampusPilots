from collections.abc import AsyncIterator
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
from app.modules.platform.repositories import AuditLogRepository, UserListItem, UserRepository


class UserNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="USER_NOT_FOUND",
            message="用户不存在",
        )


class RoleNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="ROLE_NOT_FOUND",
            message="角色不存在",
        )


class ResourceVersionConflict(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="RESOURCE_VERSION_CONFLICT",
            message="数据已被其他操作更新，请刷新后重试",
        )


class UserRoleService:
    """Replace a user's complete role set in a caller-owned transaction."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        user_repository: UserRepository,
        audit_service: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._user_repository = user_repository
        self._audit_service = audit_service
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def replace_user_roles(
        self,
        *,
        actor: AuthenticatedUser,
        user_id: UUID,
        role_ids: list[UUID],
        expected_version: int,
        request_id: str,
    ) -> UserListItem:
        async with self._session.begin():
            user = await self._user_repository.get_for_update(user_id)
            if user is None:
                raise UserNotFound()
            before_roles = await self._user_repository.get_roles_for_user(user.id)
            if user.version != expected_version:
                raise ResourceVersionConflict()

            roles = await self._user_repository.get_roles_by_ids(role_ids)
            if len(roles) != len(role_ids):
                raise RoleNotFound()

            now = self._current_time()
            if not await self._user_repository.bump_version_if_match(
                user.id,
                expected_version,
                now,
            ):
                raise ResourceVersionConflict()
            await self._user_repository.clear_roles(user.id)
            self._user_repository.add_roles(
                user.id,
                [role.id for role in roles],
                actor.user_id,
            )
            user.version = expected_version + 1
            user.updated_at = now

            self._audit_service.record_success(
                action="user.roles.replace",
                resource_type="user",
                resource_id=str(user.id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                before_data={
                    "user_id": str(user.id),
                    "version": expected_version,
                    "role_ids": [str(role.id) for role in before_roles],
                },
                after_data={
                    "user_id": str(user.id),
                    "version": user.version,
                    "role_ids": [str(role.id) for role in roles],
                },
            )
            return UserListItem(user=user, roles=tuple(roles))

    def _current_time(self) -> datetime:
        now = self._now()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


@asynccontextmanager
async def user_role_service_context(
    settings: Settings,
) -> AsyncIterator[UserRoleService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield UserRoleService(
                session=session,
                user_repository=UserRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()
