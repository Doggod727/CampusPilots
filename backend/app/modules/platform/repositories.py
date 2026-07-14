from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import (
    AppConfig,
    AuditLog,
    IdempotencyRecord,
    ModerationCase,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    SensitiveWord,
    User,
    UserRole,
)

AUTH_MAX_FAILED_LOGINS_KEY = "auth.max_failed_logins"
AUTH_LOCK_MINUTES_KEY = "auth.lock_minutes"
AUTH_POLICY_KEYS = (AUTH_MAX_FAILED_LOGINS_KEY, AUTH_LOCK_MINUTES_KEY)


class InvalidAuthPolicy(Exception):
    """Raised when the persisted authentication policy is unsafe to use."""

    def __init__(self) -> None:
        super().__init__("Authentication policy configuration is invalid.")


@dataclass(frozen=True)
class AuthLoginPolicy:
    max_failed_logins: int
    lock_minutes: int


@dataclass(frozen=True)
class UserListQuery:
    page: int
    page_size: int
    q: str | None = None
    status: str | None = None
    role_id: UUID | None = None
    sort: str = "-created_at"


@dataclass(frozen=True)
class UserListItem:
    user: User
    roles: tuple[Role, ...]


@dataclass(frozen=True)
class UserListPage:
    items: tuple[UserListItem, ...]
    total: int


@dataclass(frozen=True)
class RoleListItem:
    role: Role
    permissions: tuple[Permission, ...]
    user_count: int


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

    async def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(
            User.email == email,
            User.deleted_at.is_(None),
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_roles_by_ids(self, role_ids: list[UUID]) -> list[Role]:
        result = await self._session.execute(
            select(Role).where(Role.id.in_(role_ids)).order_by(Role.code)
        )
        return list(result.scalars().all())

    def add(self, user: User) -> None:
        self._session.add(user)

    def add_roles(
        self,
        user_id: UUID,
        role_ids: list[UUID],
        assigned_by: UUID,
    ) -> None:
        for role_id in role_ids:
            self._session.add(
                UserRole(
                    user_id=user_id,
                    role_id=role_id,
                    assigned_by=assigned_by,
                )
            )

    async def get_by_id(self, user_id: UUID) -> User | None:
        statement = select(User).where(
            User.id == user_id,
            User.deleted_at.is_(None),
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_for_update(self, user_id: UUID) -> User | None:
        result = await self._session.execute(
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_roles_for_user(self, user_id: UUID) -> list[Role]:
        result = await self._session.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.code)
        )
        return list(result.scalars().all())

    async def bump_version_if_match(
        self,
        user_id: UUID,
        expected_version: int,
        updated_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.version == expected_version,
                User.deleted_at.is_(None),
            )
            .values(version=User.version + 1, updated_at=updated_at)
        )
        return result.rowcount == 1

    async def clear_roles(self, user_id: UUID) -> int:
        result = await self._session.execute(
            delete(UserRole).where(UserRole.user_id == user_id)
        )
        return result.rowcount

    async def update_if_version(
        self,
        user_id: UUID,
        expected_version: int,
        updates: dict[str, object],
        updated_at: datetime,
    ) -> bool:
        values = dict(updates)
        values.update(version=User.version + 1, updated_at=updated_at)
        result = await self._session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.version == expected_version,
                User.deleted_at.is_(None),
            )
            .values(**values)
        )
        return result.rowcount == 1

    async def count_active_super_admins(self) -> int:
        result = await self._session.execute(
            select(func.count(User.id))
            .select_from(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                User.status == "active",
                User.deleted_at.is_(None),
                Role.code == "super_admin",
            )
        )
        return result.scalar_one()


    async def get_summary_by_id(self, user_id: UUID) -> UserListItem | None:
        user = await self.get_by_id(user_id)
        if user is None:
            return None

        roles_result = await self._session.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
            .order_by(Role.code)
        )
        return UserListItem(user=user, roles=tuple(roles_result.scalars().all()))

    async def list_page(self, query: UserListQuery) -> UserListPage:
        predicates = self._list_predicates(query)
        count_result = await self._session.execute(
            select(func.count()).select_from(User).where(*predicates)
        )
        total = count_result.scalar_one()

        users_result = await self._session.execute(
            select(User)
            .where(*predicates)
            .order_by(*self._list_order(query.sort))
            .limit(query.page_size)
            .offset((query.page - 1) * query.page_size)
        )
        users = list(users_result.scalars().all())
        if not users:
            return UserListPage(items=(), total=total)

        user_ids = [user.id for user in users]
        roles_result = await self._session.execute(
            select(UserRole.user_id, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids))
            .order_by(UserRole.user_id, Role.code)
        )
        roles_by_user: dict[UUID, list[Role]] = {user.id: [] for user in users}
        for user_id, role in roles_result.all():
            roles_by_user[user_id].append(role)

        return UserListPage(
            items=tuple(
                UserListItem(user=user, roles=tuple(roles_by_user[user.id]))
                for user in users
            ),
            total=total,
        )

    @staticmethod
    def _list_predicates(query: UserListQuery) -> list[object]:
        predicates: list[object] = [User.deleted_at.is_(None)]
        if query.q is not None and query.q.strip():
            pattern = f"%{query.q.strip()}%"
            predicates.append(
                or_(
                    User.username.ilike(pattern),
                    User.display_name.ilike(pattern),
                    User.email.ilike(pattern),
                )
            )
        if query.status is not None:
            predicates.append(User.status == query.status)
        if query.role_id is not None:
            predicates.append(
                User.id.in_(
                    select(UserRole.user_id).where(UserRole.role_id == query.role_id)
                )
            )
        return predicates

    @staticmethod
    def _list_order(sort: str) -> tuple[object, object]:
        orders: dict[str, tuple[object, object]] = {
            "created_at": (User.created_at.asc(), User.id.asc()),
            "-created_at": (User.created_at.desc(), User.id.asc()),
            "username": (User.username.asc(), User.id.asc()),
            "-username": (User.username.desc(), User.id.asc()),
            "last_login_at": (User.last_login_at.asc().nulls_last(), User.id.asc()),
            "-last_login_at": (
                User.last_login_at.desc().nulls_last(),
                User.id.asc(),
            ),
        }
        return orders[sort]


class RbacReadRepository:
    """Read-only role and permission catalog queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_roles(self) -> tuple[RoleListItem, ...]:
        roles_result = await self._session.execute(
            select(Role).order_by(Role.code)
        )
        roles = list(roles_result.scalars().all())
        if not roles:
            return ()

        role_ids = [role.id for role in roles]
        permissions_result = await self._session.execute(
            select(RolePermission.role_id, Permission)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id.in_(role_ids))
            .order_by(RolePermission.role_id, Permission.code)
        )
        permissions_by_role: dict[UUID, list[Permission]] = {
            role_id: [] for role_id in role_ids
        }
        for role_id, permission in permissions_result.all():
            permissions_by_role[role_id].append(permission)

        counts_result = await self._session.execute(
            select(UserRole.role_id, func.count(UserRole.user_id))
            .join(User, User.id == UserRole.user_id)
            .where(User.deleted_at.is_(None))
            .group_by(UserRole.role_id)
        )
        user_counts = {role_id: count for role_id, count in counts_result.all()}
        return tuple(
            RoleListItem(
                role=role,
                permissions=tuple(permissions_by_role[role.id]),
                user_count=user_counts.get(role.id, 0),
            )
            for role in roles
        )

    async def list_permissions(self, module: str | None = None) -> list[Permission]:
        statement = select(Permission)
        if module is not None:
            statement = statement.where(Permission.module == module)
        result = await self._session.execute(
            statement.order_by(Permission.module, Permission.code)
        )
        return list(result.scalars().all())

    async def get_role(self, role_id: UUID) -> RoleListItem | None:
        role_result = await self._session.execute(
            select(Role).where(Role.id == role_id)
        )
        role = role_result.scalar_one_or_none()
        if role is None:
            return None
        permissions_result = await self._session.execute(
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
            .order_by(Permission.code)
        )
        counts_result = await self._session.execute(
            select(func.count(UserRole.user_id))
            .join(User, User.id == UserRole.user_id)
            .where(
                UserRole.role_id == role.id,
                User.deleted_at.is_(None),
            )
        )
        return RoleListItem(
            role=role,
            permissions=tuple(permissions_result.scalars().all()),
            user_count=counts_result.scalar_one(),
        )


class RbacWriteRepository(RbacReadRepository):
    """Role catalog writes within a caller-owned transaction."""

    async def get_role_by_code(self, code: str) -> Role | None:
        result = await self._session.execute(
            select(Role).where(Role.code == code)
        )
        return result.scalar_one_or_none()

    async def get_permissions_by_ids(
        self,
        permission_ids: list[UUID],
    ) -> list[Permission]:
        result = await self._session.execute(
            select(Permission)
            .where(Permission.id.in_(permission_ids))
            .order_by(Permission.code)
        )
        return list(result.scalars().all())

    def add_role(self, role: Role) -> None:
        self._session.add(role)

    def add_role_permissions(self, role_id: UUID, permission_ids: list[UUID]) -> None:
        for permission_id in permission_ids:
            self._session.add(
                RolePermission(role_id=role_id, permission_id=permission_id)
            )

    async def update_role_if_version(
        self,
        role_id: UUID,
        expected_version: int,
        updates: dict[str, object],
        updated_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(Role)
            .where(Role.id == role_id, Role.version == expected_version)
            .values(**updates, version=Role.version + 1, updated_at=updated_at)
        )
        return result.rowcount == 1

    async def clear_role_permissions(self, role_id: UUID) -> int:
        result = await self._session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )
        return result.rowcount

    async def get_role_for_update(self, role_id: UUID) -> Role | None:
        result = await self._session.execute(
            select(Role).where(Role.id == role_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def count_role_assignments(self, role_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count(UserRole.user_id)).where(UserRole.role_id == role_id)
        )
        return int(result.scalar_one())

    async def delete_role(self, role_id: UUID) -> bool:
        result = await self._session.execute(delete(Role).where(Role.id == role_id))
        return result.rowcount == 1


class AuthPolicyRepository:
    """Read the persisted login-lock policy within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_login_policy(self) -> AuthLoginPolicy:
        statement = select(
            AppConfig.key,
            AppConfig.value,
            AppConfig.value_type,
        ).where(AppConfig.key.in_(AUTH_POLICY_KEYS))
        result = await self._session.execute(statement)
        rows = result.all()
        values: dict[str, object] = {}
        for key, value, value_type in rows:
            if (
                key not in AUTH_POLICY_KEYS
                or key in values
                or value_type != "integer"
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise InvalidAuthPolicy
            values[key] = value

        if set(values) != set(AUTH_POLICY_KEYS):
            raise InvalidAuthPolicy

        return AuthLoginPolicy(
            max_failed_logins=values[AUTH_MAX_FAILED_LOGINS_KEY],
            lock_minutes=values[AUTH_LOCK_MINUTES_KEY],
        )


class AuditLogRepository:
    """Read and append audit events within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, audit_log: AuditLog) -> None:
        self._session.add(audit_log)

    async def list_page(
        self,
        *,
        page: int,
        page_size: int,
        actor_user_id: UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        request_id: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> tuple[list[AuditLog], int]:
        predicates = []
        if actor_user_id is not None:
            predicates.append(AuditLog.actor_user_id == actor_user_id)
        if action is not None:
            predicates.append(AuditLog.action == action)
        if resource_type is not None:
            predicates.append(AuditLog.resource_type == resource_type)
        if request_id is not None:
            predicates.append(AuditLog.request_id == request_id)
        if from_time is not None:
            predicates.append(AuditLog.created_at >= from_time)
        if to_time is not None:
            predicates.append(AuditLog.created_at <= to_time)
        count_result = await self._session.execute(
            select(func.count(AuditLog.id)).where(*predicates)
        )
        rows_result = await self._session.execute(
            select(AuditLog)
            .where(*predicates)
            .order_by(AuditLog.created_at.desc(), AuditLog.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows_result.scalars().all()), int(count_result.scalar_one())

    async def get_by_id(self, audit_id: UUID) -> AuditLog | None:
        result = await self._session.execute(
            select(AuditLog).where(AuditLog.id == audit_id)
        )
        return result.scalar_one_or_none()


class IdempotencyRecordRepository:
    """Idempotency persistence within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_scope_for_update(
        self,
        user_id: UUID,
        endpoint: str,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        statement = (
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.endpoint == endpoint,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    def add(self, record: IdempotencyRecord) -> None:
        self._session.add(record)

    async def complete(
        self,
        record_id: UUID,
        response_status: int,
        response_body: object,
        resource_type: str | None,
        resource_id: str | None,
    ) -> bool:
        statement = (
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.response_status.is_(None),
            )
            .values(
                response_status=response_status,
                response_body=response_body,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

@dataclass(frozen=True)
class LoginFailureState:
    failed_login_count: int
    status: str
    locked_until: datetime | None


class UserAuthRepository:
    """Atomic login-state updates within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_failed_login(
        self,
        user_id: UUID,
        max_failed_logins: int,
        locked_until: datetime,
    ) -> LoginFailureState | None:
        next_failure_count = User.failed_login_count + 1
        lock_account = next_failure_count >= max_failed_logins
        statement = (
            update(User)
            .where(
                User.id == user_id,
                User.deleted_at.is_(None),
                User.status != "disabled",
            )
            .values(
                failed_login_count=next_failure_count,
                locked_until=case(
                    (lock_account, locked_until),
                    else_=User.locked_until,
                ),
                status=case(
                    (lock_account, "locked"),
                    else_=User.status,
                ),
            )
            .returning(
                User.failed_login_count,
                User.status,
                User.locked_until,
            )
        )
        result = await self._session.execute(statement)
        row = result.one_or_none()
        if row is None:
            return None
        failed_login_count, status, returned_locked_until = row
        return LoginFailureState(
            failed_login_count=failed_login_count,
            status=status,
            locked_until=returned_locked_until,
        )

    async def record_successful_login(
        self,
        user_id: UUID,
        logged_in_at: datetime,
    ) -> bool:
        statement = (
            update(User)
            .where(
                User.id == user_id,
                User.deleted_at.is_(None),
                User.status != "disabled",
            )
            .values(
                failed_login_count=0,
                locked_until=None,
                status="active",
                last_login_at=logged_in_at,
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

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


class SensitiveWordRepository:
    """Sensitive-word rule persistence within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_page(
        self,
        *,
        page: int,
        page_size: int,
        query: str | None = None,
        scope: str | None = None,
        enabled: bool | None = None,
    ) -> tuple[list[SensitiveWord], int]:
        predicates = []
        if query:
            predicates.append(func.lower(SensitiveWord.word).contains(query.lower()))
        if scope is not None:
            predicates.append(SensitiveWord.scope == scope)
        if enabled is not None:
            predicates.append(SensitiveWord.enabled.is_(enabled))
        count_result = await self._session.execute(
            select(func.count(SensitiveWord.id)).where(*predicates)
        )
        rows_result = await self._session.execute(
            select(SensitiveWord)
            .where(*predicates)
            .order_by(SensitiveWord.created_at.desc(), SensitiveWord.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows_result.scalars().all()), int(count_result.scalar_one())

    async def get_by_rule(
        self,
        *,
        word: str,
        match_type: str,
        scope: str,
    ) -> SensitiveWord | None:
        result = await self._session.execute(
            select(SensitiveWord).where(
                func.lower(SensitiveWord.word) == word.lower(),
                SensitiveWord.match_type == match_type,
                SensitiveWord.scope == scope,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, word_id: UUID) -> SensitiveWord | None:
        result = await self._session.execute(
            select(SensitiveWord).where(SensitiveWord.id == word_id)
        )
        return result.scalar_one_or_none()

    async def list_enabled_for_scope(self, scope: str) -> list[SensitiveWord]:
        result = await self._session.execute(
            select(SensitiveWord)
            .where(
                SensitiveWord.enabled.is_(True),
                SensitiveWord.scope.in_((scope, "all")),
            )
            .order_by(SensitiveWord.created_at, SensitiveWord.id)
        )
        return list(result.scalars().all())

    def add(self, rule: SensitiveWord) -> None:
        self._session.add(rule)

    async def delete(self, word_id: UUID) -> bool:
        result = await self._session.execute(
            delete(SensitiveWord).where(SensitiveWord.id == word_id)
        )
        return result.rowcount == 1


class ModerationCaseRepository:
    """Moderation case persistence within a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_page(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        risk_level: str | None = None,
        target_module: str | None = None,
        sort: str = "-created_at",
    ) -> tuple[list[ModerationCase], int]:
        predicates = []
        if status is not None:
            predicates.append(ModerationCase.status == status)
        if risk_level is not None:
            predicates.append(ModerationCase.risk_level == risk_level)
        if target_module is not None:
            predicates.append(ModerationCase.target_module == target_module)
        sort_column = {
            "created_at": ModerationCase.created_at,
            "-created_at": ModerationCase.created_at.desc(),
            "risk_level": ModerationCase.risk_level,
            "-risk_level": ModerationCase.risk_level.desc(),
        }[sort]
        count_result = await self._session.execute(
            select(func.count(ModerationCase.id)).where(*predicates)
        )
        rows_result = await self._session.execute(
            select(ModerationCase)
            .where(*predicates)
            .order_by(sort_column, ModerationCase.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows_result.scalars().all()), int(count_result.scalar_one())

    async def get_by_id(self, case_id: UUID) -> ModerationCase | None:
        result = await self._session.execute(
            select(ModerationCase).where(ModerationCase.id == case_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, case_id: UUID) -> ModerationCase | None:
        result = await self._session.execute(
            select(ModerationCase).where(ModerationCase.id == case_id).with_for_update()
        )
        return result.scalar_one_or_none()

    def add(self, case: ModerationCase) -> None:
        self._session.add(case)

    async def decide_if_version(
        self,
        *,
        case_id: UUID,
        expected_version: int,
        status: str,
        reviewer_id: UUID,
        decision_reason: str,
        reviewed_at: datetime,
        updated_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(ModerationCase)
            .where(
                ModerationCase.id == case_id,
                ModerationCase.version == expected_version,
                ModerationCase.status == "pending",
            )
            .values(
                status=status, reviewer_id=reviewer_id,
                decision_reason=decision_reason, reviewed_at=reviewed_at,
                updated_at=updated_at, version=ModerationCase.version + 1,
            )
        )
        return result.rowcount == 1
