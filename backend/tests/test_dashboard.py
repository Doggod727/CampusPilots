import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.dashboard import DashboardQueryService, InvalidDashboardRange
from app.modules.platform.dashboard_routes import get_service
from app.modules.platform.repositories import DashboardRepository


def _actor(*permissions: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员", email=None, department=None,
        status="active", roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=permissions, last_login_at=None, created_at=datetime.now(UTC), version=1,
    )


def test_dashboard_service_returns_m4_summary_and_zero_unregistered_metrics() -> None:
    repository = MagicMock(spec=DashboardRepository)
    repository.summary = AsyncMock(return_value={"active_users": 3, "moderation_pending": 2, "audit_events": 4, "refresh_tokens": 1})
    service = DashboardQueryService(session=MagicMock(), repository=repository)
    result = asyncio.run(service.get_metrics(from_date=date(2026, 7, 1), to_date=date(2026, 7, 3)))
    assert result["summary"]["active_users"] == 3
    assert result["summary"]["moderation_pending"] == 2
    assert result["summary"]["chat_messages"] == 0
    assert len(result["series"]["active_users"]) == 3


def test_dashboard_service_rejects_invalid_range() -> None:
    repository = MagicMock(spec=DashboardRepository)
    service = DashboardQueryService(session=MagicMock(), repository=repository)
    try:
        asyncio.run(service.get_metrics(from_date=date(2026, 7, 3), to_date=date(2026, 7, 1)))
    except InvalidDashboardRange:
        pass
    else:
        raise AssertionError("invalid range must be rejected")
    repository.summary.assert_not_called()


def test_dashboard_route_returns_metrics_and_requires_permission() -> None:
    service = MagicMock(spec=DashboardQueryService)
    service.get_metrics = AsyncMock(return_value={
        "from": "2026-07-01", "to": "2026-07-02", "granularity": "day",
        "summary": {"active_users": 1, "chat_messages": 0, "work_orders": 0, "posts": 0, "lost_found_items": 0, "moderation_pending": 0, "llm_tokens": 0},
        "series": {"active_users": [{"period": "2026-07-01", "value": 1}]},
    })
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("dashboard:read")
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/dashboard/metrics")
    assert response.status_code == 200
    assert response.json()["data"]["summary"]["active_users"] == 1
