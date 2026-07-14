from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.models import User
from app.modules.platform.passwords import PasswordHasher
from app.modules.platform.repositories import (
    AuditLogRepository,
    IdempotencyRecordRepository,
    UserListItem,
    UserRepository,
)
from app.modules.platform.user_schemas import UserSummaryData, user_summary
from app.shared.responses import SuccessResponse


class DuplicateResource(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="DUPLICATE_RESOURCE",
            message="用户资源已存在",
        )


class RoleNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="ROLE_NOT_FOUND",
            message="角色不存在",
        )


@dataclass(frozen=True)
class CreateUserResult:
    status_code: int
    request_id: str
    body: dict[str, Any] = field(repr=False)


class UserAdminService:
    """User administration use cases within a caller-owned async session."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        user_repository: UserRepository,
        idempotency_service: IdempotencyService,
        audit_service: AuditService,
        password_hasher: PasswordHasher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._user_repository = user_repository
        self._idempotency_service = idempotency_service
        self._audit_service = audit_service
        self._password_hasher = password_hasher
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def create_user(
        self,
        *,
        actor: AuthenticatedUser,
        username: str,
        password: str,
        display_name: str,
        email: str | None,
        department: str | None,
        role_ids: list[UUID],
        idempotency_key: str,
        request_id: str,
        request_body: Mapping[str, object],
    ) -> CreateUserResult:
        async with self._session.begin():
            decision = await self._idempotency_service.begin(
                user_id=actor.user_id,
                endpoint="POST /api/v1/users",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                return CreateUserResult(
                    status_code=decision.replay.response_status,
                    request_id=str(decision.replay.response_body["request_id"]),
                    body=dict(decision.replay.response_body),
                )
            if decision.pending:
                raise IdempotencyConflict()

            roles = await self._user_repository.get_roles_by_ids(role_ids)
            if len(roles) != len(role_ids):
                raise RoleNotFound()
            if await self._user_repository.get_by_username(username) is not None:
                raise DuplicateResource()
            if email is not None and await self._user_repository.get_by_email(email):
                raise DuplicateResource()

            now = self._current_time()
            user = User(
                id=uuid4(),
                username=username,
                password_hash=self._password_hasher.hash(password),
                display_name=display_name,
                email=email,
                department=department,
                status="active",
                failed_login_count=0,
                locked_until=None,
                last_login_at=None,
                password_changed_at=now,
                version=1,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._user_repository.add(user)
            try:
                await self._session.flush()
            except IntegrityError:
                raise DuplicateResource() from None
            self._user_repository.add_roles(
                user.id,
                [role.id for role in roles],
                actor.user_id,
            )
            summary = user_summary(UserListItem(user=user, roles=tuple(roles)))
            response = SuccessResponse(
                data=summary,
                request_id=request_id,
                timestamp=now,
            )
            body = response.model_dump(mode="json")
            self._audit_service.record_success(
                action="user.create",
                resource_type="user",
                resource_id=str(user.id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                after_data={
                    "id": str(user.id),
                    "username": user.username,
                    "status": user.status,
                    "role_ids": [str(role.id) for role in roles],
                },
            )
            completed = await self._idempotency_service.complete(
                record_id=decision.record_id,
                response_status=201,
                response_body=body,
                resource_type="user",
                resource_id=str(user.id),
            )
            if not completed:
                raise DuplicateResource()
            return CreateUserResult(status_code=201, request_id=request_id, body=body)

    def _current_time(self) -> datetime:
        now = self._now()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


@asynccontextmanager
async def user_admin_service_context(
    settings: Settings,
) -> AsyncIterator[UserAdminService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield UserAdminService(
                session=session,
                user_repository=UserRepository(session),
                idempotency_service=IdempotencyService(
                    session=session,
                    repository=IdempotencyRecordRepository(session),
                ),
                audit_service=AuditService(AuditLogRepository(session)),
                password_hasher=PasswordHasher(),
            )
    finally:
        await database.dispose()
