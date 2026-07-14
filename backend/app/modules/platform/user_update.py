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
    RefreshTokenRepository,
    UserListItem,
    UserRepository,
)


class DuplicateResource(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="DUPLICATE_RESOURCE",
            message="用户资源已存在",
        )


class UserNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="USER_NOT_FOUND",
            message="用户不存在",
        )


class ResourceVersionConflict(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="RESOURCE_VERSION_CONFLICT",
            message="数据已被其他操作更新，请刷新后重试",
        )


class StatusChangeNotAllowed(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="STATUS_CHANGE_NOT_ALLOWED",
            message="该账号状态不能由此接口设置",
        )


class LastSuperAdmin(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="LAST_SUPER_ADMIN",
            message="不能禁用最后一个有效超级管理员",
        )


class UserUpdateService:
    """Update safe user fields and status in one caller-owned transaction."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        user_repository: UserRepository,
        refresh_token_repository: RefreshTokenRepository,
        audit_service: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._user_repository = user_repository
        self._refresh_token_repository = refresh_token_repository
        self._audit_service = audit_service
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def update_user(
        self,
        *,
        actor: AuthenticatedUser,
        user_id: UUID,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str,
    ) -> UserListItem:
        async with self._session.begin():
            user = await self._user_repository.get_for_update(user_id)
            if user is None:
                raise UserNotFound()
            if user.version != expected_version:
                raise ResourceVersionConflict()
            if changes.get("status") == "locked":
                raise StatusChangeNotAllowed()

            before_roles = await self._user_repository.get_roles_for_user(user.id)
            email = changes.get("email")
            if email is not None:
                existing = await self._user_repository.get_by_email(str(email))
                if existing is not None and existing.id != user.id:
                    raise DuplicateResource()

            status = changes.get("status")
            if status == "disabled" and user.status != "disabled":
                if (
                    any(role.code == "super_admin" for role in before_roles)
                    and await self._user_repository.count_active_super_admins() <= 1
                ):
                    raise LastSuperAdmin()

            now = self._current_time()
            before = self._snapshot(user, before_roles, expected_version)
            updates = dict(changes)
            if status == "active":
                updates.update(failed_login_count=0, locked_until=None)
            if status == "disabled":
                await self._refresh_token_repository.revoke_all_for_user(
                    user.id,
                    now,
                )
            if not await self._user_repository.update_if_version(
                user.id,
                expected_version,
                updates,
                now,
            ):
                raise ResourceVersionConflict()

            for field, value in updates.items():
                setattr(user, field, value)
            user.version = expected_version + 1
            user.updated_at = now
            after = self._snapshot(user, before_roles, user.version)
            self._audit_service.record_success(
                action="user.update",
                resource_type="user",
                resource_id=str(user.id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                before_data=before,
                after_data=after,
            )
            return UserListItem(user=user, roles=tuple(before_roles))

    @staticmethod
    def _snapshot(user, roles, version: int) -> dict[str, object]:
        return {
            "id": str(user.id),
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
            "department": user.department,
            "status": user.status,
            "version": version,
            "role_ids": [str(role.id) for role in roles],
        }

    def _current_time(self) -> datetime:
        now = self._now()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


@asynccontextmanager
async def user_update_service_context(
    settings: Settings,
) -> AsyncIterator[UserUpdateService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield UserUpdateService(
                session=session,
                user_repository=UserRepository(session),
                refresh_token_repository=RefreshTokenRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()
