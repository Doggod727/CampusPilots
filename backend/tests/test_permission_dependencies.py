import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser, PermissionDenied
from app.modules.platform.auth_dependencies import require_permissions


def _user(*permissions: str) -> AuthenticatedUser:
    now = datetime.now(UTC)
    return AuthenticatedUser(
        user_id=uuid4(),
        username="student01",
        display_name="张同学",
        email=None,
        department=None,
        status="active",
        roles=(
            AuthenticatedRole(
                role_id=uuid4(),
                code="student",
                name="普通学生",
            ),
        ),
        permissions=permissions,
        last_login_at=None,
        created_at=now,
        version=1,
    )


def test_require_permissions_allows_a_user_with_the_required_permission() -> None:
    user = _user("community:read", "community:write")

    result = asyncio.run(require_permissions("community:read")(user))

    assert result is user


def test_require_permissions_rejects_a_missing_single_permission() -> None:
    user = _user("community:read")

    with pytest.raises(PermissionDenied) as error:
        asyncio.run(require_permissions("user:read")(user))

    assert error.value.status_code == 403
    assert error.value.code == "AUTH_FORBIDDEN"
    assert "user:read" not in str(error.value)


def test_require_permissions_requires_every_declared_permission() -> None:
    user = _user("community:read", "user:read")

    result = asyncio.run(
        require_permissions("user:read", "community:read")(user)
    )

    assert result is user

    with pytest.raises(PermissionDenied) as error:
        asyncio.run(require_permissions("user:read", "user:write")(user))

    assert error.value.status_code == 403
    assert error.value.code == "AUTH_FORBIDDEN"
    assert "user:write" not in str(error.value)


@pytest.mark.parametrize(
    "permissions",
    [
        (),
        ("",),
        ("  ",),
        ("user:read", "user:read"),
    ],
)
def test_require_permissions_rejects_invalid_declarations(
    permissions: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        require_permissions(*permissions)
