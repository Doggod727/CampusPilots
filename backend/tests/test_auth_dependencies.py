import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import app.modules.platform.auth_dependencies as dependencies
from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser, AuthenticationRequired
from app.modules.platform.tokens import AccessClaims, InvalidAccessToken

FIXED_NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _claims() -> AccessClaims:
    return AccessClaims(
        user_id=uuid4(),
        username="student01",
        roles=("student",),
        permissions=("community:read",),
        issued_at=FIXED_NOW,
        expires_at=FIXED_NOW + timedelta(minutes=15),
        jti=uuid4(),
    )


def _user(claims: AccessClaims) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=claims.user_id,
        username=claims.username,
        display_name="张同学",
        email="student01@example.edu",
        department="计算机学院",
        status="active",
        roles=(
            AuthenticatedRole(
                role_id=uuid4(),
                code="student",
                name="普通学生",
            ),
        ),
        permissions=("community:read",),
        last_login_at=None,
        created_at=FIXED_NOW - timedelta(days=1),
        version=1,
    )


def test_missing_bearer_is_rejected_before_reading_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called() -> object:
        raise AssertionError("Settings must not be loaded for a missing bearer token.")

    monkeypatch.setattr(dependencies, "get_settings", fail_if_called)

    with pytest.raises(AuthenticationRequired) as error:
        asyncio.run(dependencies.get_access_claims(None))

    assert error.value.status_code == 401
    assert error.value.code == "AUTH_UNAUTHORIZED"


def test_invalid_bearer_is_rejected_without_database_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidTokenService:
        def __init__(self, settings: object) -> None:
            pass

        def decode_access(self, token: str) -> AccessClaims:
            raise InvalidAccessToken()

    def database_context_must_not_run(settings: object) -> object:
        raise AssertionError("Database context must not run for an invalid bearer token.")

    monkeypatch.setattr(dependencies, "TokenService", InvalidTokenService)
    monkeypatch.setattr(dependencies, "get_settings", lambda: object())
    monkeypatch.setattr(
        dependencies,
        "auth_service_context",
        database_context_must_not_run,
    )

    response = TestClient(create_app(), raise_server_exceptions=False).get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid-access-token"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_UNAUTHORIZED"
    assert "invalid-access-token" not in response.text


def test_non_bearer_scheme_is_rejected_before_reading_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called() -> object:
        raise AssertionError("Settings must not be loaded for a non-Bearer scheme.")

    monkeypatch.setattr(dependencies, "get_settings", fail_if_called)

    with pytest.raises(AuthenticationRequired):
        asyncio.run(
            dependencies.get_access_claims(
                HTTPAuthorizationCredentials(
                    scheme="Basic",
                    credentials="not-a-bearer-token",
                )
            )
        )


def test_valid_bearer_decodes_access_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _claims()

    class ValidTokenService:
        def __init__(self, settings: object) -> None:
            pass

        def decode_access(self, token: str) -> AccessClaims:
            assert token == "valid-access-token"
            return claims

    monkeypatch.setattr(dependencies, "TokenService", ValidTokenService)
    monkeypatch.setattr(dependencies, "get_settings", lambda: object())

    result = asyncio.run(
        dependencies.get_access_claims(
            HTTPAuthorizationCredentials(
                scheme="Bearer",
                credentials="valid-access-token",
            )
        )
    )

    assert result == claims


def test_valid_claims_load_current_user_only_after_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _claims()
    authenticated_user = _user(claims)
    service = MagicMock()
    service.get_current_user = AsyncMock(return_value=authenticated_user)
    seen_settings: list[object] = []

    @asynccontextmanager
    async def fake_context(settings: object):
        seen_settings.append(settings)
        yield service

    settings = object()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies, "auth_service_context", fake_context)

    result = asyncio.run(dependencies.get_authenticated_user(claims))

    assert result == authenticated_user
    assert seen_settings == [settings]
    service.get_current_user.assert_awaited_once_with(claims)
