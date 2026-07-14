from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import (
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
    UserRole,
)


class UserRepository:
    """Persistence queries for platform users within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> User | None:
        statement = select(User).where(
            User.username == username,
            User.deleted_at.is_(None),
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        statement = select(User).where(
            User.id == user_id,
            User.deleted_at.is_(None),
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()


class RbacRepository:
    """Read-only role and permission queries within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_roles_for_user(self, user_id: UUID) -> list[Role]:
        statement = (
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .join(User, User.id == UserRole.user_id)
            .where(
                User.id == user_id,
                User.deleted_at.is_(None),
            )
            .order_by(Role.code)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_permission_codes_for_user(self, user_id: UUID) -> list[str]:
        statement = (
            select(Permission.code)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                User.id == user_id,
                User.deleted_at.is_(None),
            )
            .distinct()
            .order_by(Permission.code)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())


class RefreshTokenRepository:
    """Refresh Token persistence operations within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)

    async def get_by_token_hash_for_update(
        self,
        token_hash: str,
    ) -> RefreshToken | None:
        statement = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def mark_rotated(
        self,
        jti: UUID,
        replacement_jti: UUID,
        revoked_at: datetime,
    ) -> bool:
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.jti == jti,
                RefreshToken.revoked_at.is_(None),
            )
            .values(
                revoked_at=revoked_at,
                replaced_by_jti=replacement_jti,
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def revoke_by_jti(self, jti: UUID, revoked_at: datetime) -> bool:
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.jti == jti,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def revoke_all_for_user(self, user_id: UUID, revoked_at: datetime) -> int:
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        result = await self._session.execute(statement)
        return result.rowcount
