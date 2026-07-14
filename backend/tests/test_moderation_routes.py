from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.models import ModerationCase
from app.modules.platform.moderation_routes import get_repository
from app.modules.platform.repositories import ModerationCaseRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _actor(*permissions: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def _case() -> ModerationCase:
    return ModerationCase(
        id=uuid4(), target_module="community", target_type="post", target_id=uuid4(),
        content_excerpt="摘要", risk_level="high", rule_hits=[{"rule": "id", "action": "review", "matched_text": "secret"}],
        status="pending", submitted_by=uuid4(), version=1, created_at=NOW, updated_at=NOW,
    )


def test_moderation_routes_return_safe_page_and_detail() -> None:
    case = _case()
    repository = MagicMock(spec=ModerationCaseRepository)
    repository.list_page = AsyncMock(return_value=([case], 1))
    repository.get_by_id = AsyncMock(return_value=case)
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("moderation:read")
    client = TestClient(app, raise_server_exceptions=False)

    page = client.get("/api/v1/moderation/cases", headers={"X-Request-Id": "mod-list"})
    detail = client.get(f"/api/v1/moderation/cases/{case.id}")
    assert page.status_code == 200
    assert page.json()["data"]["pagination"]["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["data"]["rule_hits"][0]["matched_text"] is None
    assert "secret" not in detail.text


def test_moderation_routes_require_permission_and_map_not_found() -> None:
    repository = MagicMock(spec=ModerationCaseRepository)
    repository.get_by_id = AsyncMock(return_value=None)
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("role:read")
    client = TestClient(app, raise_server_exceptions=False)
    forbidden = client.get(f"/api/v1/moderation/cases/{uuid4()}")
    assert forbidden.status_code == 403

    app.dependency_overrides[get_authenticated_user] = lambda: _actor("moderation:read")
    not_found = client.get(f"/api/v1/moderation/cases/{uuid4()}")
    assert not_found.status_code == 404
    assert not_found.json()["code"] == "MODERATION_CASE_NOT_FOUND"


def test_moderation_route_validates_uuid() -> None:
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: MagicMock(spec=ModerationCaseRepository)
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("moderation:read")
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/moderation/cases/not-a-uuid"
    )
    assert response.status_code == 422
