from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import (
    AccountDisabled,
    AccountLocked,
    AuthenticatedRole,
    AuthenticatedUser,
    InvalidCredentials,
    InvalidRefreshToken,
    LoginResult,
    RefreshResult,
    RefreshTokenReused,
)
from app.modules.platform.auth_routes import (
    get_auth_service,
    get_frontend_origin,
    get_refresh_cookie_secure,
)
from app.modules.platform.tokens import IssuedAccessToken, IssuedRefreshToken


class StubAuthService:
    def __init__(self, result: LoginResult | RefreshResult | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def login(self, **kwargs: object) -> LoginResult:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        assert isinstance(self._result, LoginResult)
        return self._result

    async def refresh(self, **kwargs: object) -> RefreshResult:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        assert isinstance(self._result, RefreshResult)
        return self._result

    async def logout(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result


def _login_result() -> LoginResult:
    now = datetime.now(UTC).replace(microsecond=0)
    return LoginResult(
        user=AuthenticatedUser(
            user_id=uuid4(),
            username="student01",
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
            last_login_at=now,
            created_at=now - timedelta(days=30),
            version=1,
        ),
        access_token=IssuedAccessToken(
            token="access-token-value",
            expires_at=now + timedelta(minutes=15),
            jti=uuid4(),
        ),
        refresh_token=IssuedRefreshToken(
            token="refresh-token-value",
            jti=uuid4(),
            token_hash="a" * 64,
            expires_at=now + timedelta(days=7),
        ),
    )


def _refresh_result() -> RefreshResult:
    now = datetime.now(UTC).replace(microsecond=0)
    return RefreshResult(
        access_token=IssuedAccessToken(
            token="rotated-access-token-value",
            expires_at=now + timedelta(minutes=15),
            jti=uuid4(),
        ),
        refresh_token=IssuedRefreshToken(
            token="rotated-refresh-token-value",
            jti=uuid4(),
            token_hash="b" * 64,
            expires_at=now + timedelta(days=7),
        ),
    )


def _client(
    service: StubAuthService,
    *,
    secure: bool = False,
    frontend_origin: str = "http://localhost:5173",
) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_auth_service] = lambda: service
    application.dependency_overrides[get_refresh_cookie_secure] = lambda: secure
    application.dependency_overrides[get_frontend_origin] = lambda: frontend_origin
    return TestClient(application, raise_server_exceptions=False)


def test_login_returns_openapi_data_and_sets_local_refresh_cookie() -> None:
    service = StubAuthService(_login_result())
    client = _client(service)

    response = client.post(
        "/api/v1/auth/login",
        headers={"X-Request-Id": "login-request-123", "User-Agent": "route-test"},
        json={"username": "student01", "password": "DemoPass123!"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "OK"
    assert payload["request_id"] == "login-request-123"
    assert payload["data"]["access_token"] == "access-token-value"
    assert payload["data"]["token_type"] == "Bearer"
    assert payload["data"]["expires_in"] >= 1
    assert payload["data"]["user"]["roles"][0]["code"] == "student"
    assert payload["data"]["user"]["permissions"] == ["community:read"]
    cookie = response.headers["set-cookie"]
    assert "refresh_token=refresh-token-value" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "Max-Age=" in cookie
    assert "Secure" not in cookie
    assert service.calls[0]["request_id"] == "login-request-123"
    assert service.calls[0]["user_agent"] == "route-test"


def test_login_can_set_secure_refresh_cookie_from_explicit_setting() -> None:
    service = StubAuthService(_login_result())
    client = _client(service, secure=True)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "student01", "password": "DemoPass123!"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_login_preserves_domain_error_contracts() -> None:
    cases = [
        (InvalidCredentials(), 401, "INVALID_CREDENTIALS"),
        (AccountDisabled(), 403, "ACCOUNT_DISABLED"),
        (AccountLocked(12), 423, "ACCOUNT_LOCKED"),
    ]
    for error, status_code, code in cases:
        client = _client(StubAuthService(error))

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "student01", "password": "DemoPass123!"},
        )

        assert response.status_code == status_code
        assert response.json()["code"] == code
        assert response.headers["X-Request-Id"] == response.json()["request_id"]
        if status_code == 423:
            assert response.headers["Retry-After"] == "12"


def test_login_validates_request_without_resolving_database_dependency() -> None:
    service = StubAuthService(_login_result())
    client = _client(service)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "ab", "password": "short"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert service.calls == []


def test_refresh_returns_token_data_and_rotates_cookie() -> None:
    service = StubAuthService(_refresh_result())
    client = _client(service)

    response = client.post(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "http://localhost:5173",
            "X-Request-Id": "refresh-request-123",
            "User-Agent": "refresh-route-test",
        },
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "OK"
    assert payload["request_id"] == "refresh-request-123"
    assert payload["data"] == {
        "access_token": "rotated-access-token-value",
        "token_type": "Bearer",
        "expires_in": payload["data"]["expires_in"],
    }
    assert payload["data"]["expires_in"] >= 1
    cookie = response.headers["set-cookie"]
    assert "refresh_token=rotated-refresh-token-value" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "Max-Age=" in cookie
    assert "Secure" not in cookie
    assert service.calls == [
        {
            "refresh_token": "presented-refresh-token",
            "request_id": "refresh-request-123",
            "ip_address": "testclient",
            "user_agent": "refresh-route-test",
        }
    ]


def test_refresh_can_set_secure_rotated_cookie() -> None:
    client = _client(StubAuthService(_refresh_result()), secure=True)

    response = client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://localhost:5173"},
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "error",
    [InvalidRefreshToken(), RefreshTokenReused()],
)
def test_refresh_preserves_safe_domain_error_contracts(error: Exception) -> None:
    service = StubAuthService(error)
    client = _client(service)

    response = client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://localhost:5173"},
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == error.code
    assert response.headers["X-Request-Id"] == response.json()["request_id"]


def test_refresh_missing_cookie_delegates_to_the_uniform_invalid_token_flow() -> None:
    service = StubAuthService(InvalidRefreshToken())
    client = _client(service)

    response = client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_REFRESH_TOKEN"
    assert service.calls[0]["refresh_token"] == ""


@pytest.mark.parametrize("origin", [None, "http://evil.example"])
def test_refresh_rejects_missing_or_untrusted_origin_without_resolving_service(
    origin: str | None,
) -> None:
    service = StubAuthService(_refresh_result())
    client = _client(service)
    headers = {} if origin is None else {"Origin": origin}

    response = client.post(
        "/api/v1/auth/refresh",
        headers=headers,
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_FORBIDDEN"
    assert service.calls == []


def test_refresh_accepts_configured_origin_with_trailing_slash() -> None:
    service = StubAuthService(_refresh_result())
    client = _client(service, frontend_origin="http://localhost:5173/")

    response = client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://localhost:5173"},
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 200
    assert len(service.calls) == 1


def test_logout_returns_empty_data_and_clears_cookie() -> None:
    service = StubAuthService(_refresh_result())
    client = _client(service)

    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": "http://localhost:5173",
            "X-Request-Id": "logout-request-123",
            "User-Agent": "logout-route-test",
        },
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "OK"
    assert payload["data"] == {}
    assert payload["request_id"] == "logout-request-123"
    assert response.headers["X-Request-Id"] == "logout-request-123"
    cookie = response.headers["set-cookie"]
    assert "refresh_token=\"\"" in cookie
    assert "Max-Age=0" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "Secure" not in cookie
    assert service.calls == [
        {
            "refresh_token": "presented-refresh-token",
            "request_id": "logout-request-123",
            "ip_address": "testclient",
            "user_agent": "logout-route-test",
        }
    ]


def test_logout_can_clear_cookie_with_secure_attribute() -> None:
    client = _client(StubAuthService(_refresh_result()), secure=True)

    response = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:5173"},
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_logout_missing_cookie_remains_idempotent() -> None:
    service = StubAuthService(_refresh_result())
    client = _client(service)

    response = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {}
    assert service.calls[0]["refresh_token"] == ""


@pytest.mark.parametrize("origin", [None, "http://evil.example"])
def test_logout_rejects_untrusted_origin_without_resolving_service(
    origin: str | None,
) -> None:
    service = StubAuthService(_refresh_result())
    client = _client(service)
    headers = {} if origin is None else {"Origin": origin}

    response = client.post(
        "/api/v1/auth/logout",
        headers=headers,
        cookies={"refresh_token": "presented-refresh-token"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_FORBIDDEN"
    assert service.calls == []
