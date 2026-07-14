from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import (
    AccountDisabled,
    AccountLocked,
    AuthenticatedRole,
    AuthenticatedUser,
    InvalidCredentials,
    LoginResult,
)
from app.modules.platform.auth_routes import (
    get_auth_service,
    get_refresh_cookie_secure,
)
from app.modules.platform.tokens import IssuedAccessToken, IssuedRefreshToken


class StubAuthService:
    def __init__(self, result: LoginResult | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def login(self, **kwargs: object) -> LoginResult:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


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


def _client(service: StubAuthService, *, secure: bool = False) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_auth_service] = lambda: service
    application.dependency_overrides[get_refresh_cookie_secure] = lambda: secure
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
