import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import (
    IdempotencyConflict,
    IdempotencyDecision,
    IdempotencyReplay,
)
from app.modules.platform.models import Role, User
from app.modules.platform.repositories import UserListItem
from app.modules.platform.user_admin import (
    DuplicateResource,
    RoleNotFound,
    UserAdminService,
)

FIXED_NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class _PasswordHasher:
    def __init__(self) -> None:
        self.passwords: list[str] = []

    def hash(self, password: str) -> str:
        self.passwords.append(password)
        return "argon2-hash-not-plaintext"


def _session() -> MagicMock:
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    session.flush = AsyncMock()
    return session


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        username="admin01",
        display_name="管理员",
        email=None,
        department=None,
        status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="超级管理员"),),
        permissions=("user:write",),
        last_login_at=FIXED_NOW,
        created_at=FIXED_NOW,
        version=1,
    )


def _service(
    *,
    roles: list[Role] | None = None,
    existing_user: User | None = None,
    existing_email: User | None = None,
    decision: IdempotencyDecision | None = None,
) -> tuple[UserAdminService, dict[str, MagicMock], _PasswordHasher]:
    session = _session()
    user_repository = MagicMock()
    user_repository.get_roles_by_ids = AsyncMock(return_value=roles or [])
    user_repository.get_by_username = AsyncMock(return_value=existing_user)
    user_repository.get_by_email = AsyncMock(return_value=existing_email)
    idempotency = MagicMock()
    idempotency.begin = AsyncMock(
        return_value=decision or IdempotencyDecision(record_id=uuid4())
    )
    idempotency.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    hasher = _PasswordHasher()
    service = UserAdminService(
        session=session,
        user_repository=user_repository,
        idempotency_service=idempotency,
        audit_service=audit,
        password_hasher=hasher,
        now=lambda: FIXED_NOW,
    )
    return service, {
        "session": session,
        "repository": user_repository,
        "idempotency": idempotency,
        "audit": audit,
    }, hasher


def _role(code: str = "student") -> Role:
    return Role(id=uuid4(), code=code, name=code.title())


def test_create_user_hashes_password_binds_roles_audits_and_completes_idempotency() -> None:
    actor = _actor()
    role = _role()
    service, mocks, hasher = _service(roles=[role])

    result = asyncio.run(
        service.create_user(
            actor=actor,
            username="student02",
            password="DemoPass123!",
            display_name="李同学",
            email="student02@example.edu",
            department="计算机学院",
            role_ids=[role.id],
            idempotency_key="create-user-key",
            request_id="create-user-request-123",
            request_body={"username": "student02", "password": "DemoPass123!"},
        )
    )

    user = mocks["repository"].add.call_args.args[0]
    assert result.status_code == 201
    assert result.body["data"]["username"] == "student02"
    assert result.request_id == "create-user-request-123"
    assert user.password_hash == "argon2-hash-not-plaintext"
    assert "DemoPass123!" not in repr(result)
    assert hasher.passwords == ["DemoPass123!"]
    mocks["repository"].add_roles.assert_called_once_with(
        user.id,
        [role.id],
        actor.user_id,
    )
    mocks["audit"].record_success.assert_called_once()
    audit_kwargs = mocks["audit"].record_success.call_args.kwargs
    assert audit_kwargs["action"] == "user.create"
    assert "password" not in str(audit_kwargs)
    mocks["idempotency"].complete.assert_awaited_once()
    mocks["session"].flush.assert_awaited_once()
    mocks["session"].commit.assert_not_called()
    mocks["session"].rollback.assert_not_called()


def test_create_user_replays_saved_response_without_hashing_or_writing() -> None:
    actor = _actor()
    replay_body = {
        "code": "OK",
        "message": "success",
        "data": {"id": str(uuid4())},
        "request_id": "first-request-123",
        "timestamp": FIXED_NOW.isoformat(),
    }
    decision = IdempotencyDecision(
        record_id=uuid4(),
        replay=IdempotencyReplay(
            response_status=201,
            response_body=replay_body,
            resource_type="user",
            resource_id=replay_body["data"]["id"],
        ),
    )
    service, mocks, hasher = _service(decision=decision)

    result = asyncio.run(
        service.create_user(
            actor=actor,
            username="student02",
            password="DemoPass123!",
            display_name="李同学",
            email=None,
            department=None,
            role_ids=[uuid4()],
            idempotency_key="create-user-key",
            request_id="second-request-123",
            request_body={"username": "student02", "password": "DemoPass123!"},
        )
    )

    assert result.body == replay_body
    assert result.request_id == "first-request-123"
    assert hasher.passwords == []
    mocks["repository"].add.assert_not_called()
    mocks["repository"].get_roles_by_ids.assert_not_awaited()
    mocks["idempotency"].complete.assert_not_awaited()


@pytest.mark.parametrize("existing", ["username", "email"])
def test_create_user_rejects_duplicate_identity(existing: str) -> None:
    actor = _actor()
    role = _role()
    existing_user = User(id=uuid4(), username="student02", display_name="已有")
    service, mocks, _ = _service(
        roles=[role],
        existing_user=existing_user if existing == "username" else None,
        existing_email=existing_user if existing == "email" else None,
    )

    with pytest.raises(DuplicateResource) as error:
        asyncio.run(
            service.create_user(
                actor=actor,
                username="student02",
                password="DemoPass123!",
                display_name="李同学",
                email="student02@example.edu",
                department=None,
                role_ids=[role.id],
                idempotency_key="create-user-key",
                request_id="create-user-request-123",
                request_body={"username": "student02"},
            )
        )

    assert error.value.code == "DUPLICATE_RESOURCE"
    assert "student02" not in str(error.value)
    mocks["repository"].add.assert_not_called()


def test_create_user_rejects_missing_role_without_hashing() -> None:
    actor = _actor()
    service, mocks, hasher = _service(roles=[])

    with pytest.raises(RoleNotFound) as error:
        asyncio.run(
            service.create_user(
                actor=actor,
                username="student02",
                password="DemoPass123!",
                display_name="李同学",
                email=None,
                department=None,
                role_ids=[uuid4()],
                idempotency_key="create-user-key",
                request_id="create-user-request-123",
                request_body={"username": "student02"},
            )
        )

    assert error.value.code == "ROLE_NOT_FOUND"
    assert hasher.passwords == []
    mocks["repository"].add.assert_not_called()


def test_create_user_maps_pending_idempotency_to_safe_conflict() -> None:
    actor = _actor()
    service, _, _ = _service(
        decision=IdempotencyDecision(record_id=uuid4(), pending=True)
    )

    with pytest.raises(IdempotencyConflict) as error:
        asyncio.run(
            service.create_user(
                actor=actor,
                username="student02",
                password="DemoPass123!",
                display_name="李同学",
                email=None,
                department=None,
                role_ids=[uuid4()],
                idempotency_key="create-user-key",
                request_id="create-user-request-123",
                request_body={"username": "student02"},
            )
        )

    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert "DemoPass123!" not in str(error.value)
