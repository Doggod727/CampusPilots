from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.config_routes import get_service
from app.modules.platform.config_service import ConfigService
from app.modules.platform.models import AppConfig

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _actor(*permissions: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def _config() -> AppConfig:
    return AppConfig(key="demo.limit", namespace="demo", value=1, value_type="integer",
                     description="demo", editable=True, version=1, updated_at=NOW, created_at=NOW)


def test_config_routes_list_and_update_with_permissions() -> None:
    config = _config()
    service = MagicMock(spec=ConfigService)
    service.list = AsyncMock(return_value=[config])
    service.update = AsyncMock(return_value=config)
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("config:read", "config:write")
    client = TestClient(app, raise_server_exceptions=False)
    listed = client.get("/api/v1/configs", headers={"X-Request-Id": "config-list"})
    updated = client.patch("/api/v1/configs/demo.limit", json={"value": 2, "version": 1})
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["key"] == "demo.limit"
    assert updated.status_code == 200
    service.update.assert_awaited_once()


def test_config_routes_require_permissions_and_validate_payload() -> None:
    app = create_app()
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("user:read")
    client = TestClient(app, raise_server_exceptions=False)
    forbidden = client.get("/api/v1/configs")
    assert forbidden.status_code == 403
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("config:write")
    app.dependency_overrides[get_service] = lambda: MagicMock(spec=ConfigService)
    invalid = client.patch("/api/v1/configs/Bad Key", json={"value": 1, "version": 1})
    assert invalid.status_code in {404, 422}
