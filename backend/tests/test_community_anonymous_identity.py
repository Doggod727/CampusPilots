import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.main import create_app
from app.modules.community.anonymous_identity import (
    AnonymousIdentityData, AnonymousIdentityNotFound, AnonymousIdentityRepository,
    AnonymousIdentityService, HistoricalIdentity, PlatformHistoricalIdentityAdapter,
)
from app.modules.community.anonymous_identity_routes import get_anonymous_identity_service
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
ACTOR_ID = UUID("90000000-0000-4000-8000-000000000003")
AUTHOR_ID = UUID("90000000-0000-4000-8000-000000000001")
TARGET_ID = UUID("75000000-0000-4000-8000-000000000001")


def _actor(permissions=("community:anonymous_identity:read",)):
    return AuthenticatedUser(user_id=ACTOR_ID, username="admin01", display_name="管理员",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "super_admin", "管理员"),), permissions=permissions,
        last_login_at=None, created_at=NOW, version=1)


def _service(*, author_id=AUTHOR_ID, identity=True):
    session = MagicMock()
    @asynccontextmanager
    async def begin(): yield
    session.begin.side_effect = begin; session.flush = AsyncMock()
    repo = MagicMock(); repo.get_author = AsyncMock(return_value=author_id)
    identities = MagicMock(); identities.get = AsyncMock(return_value=
        HistoricalIdentity(AUTHOR_ID, "student01", "学生一号") if identity else None)
    audit = MagicMock()
    return AnonymousIdentityService(session=session, repository=repo, identities=identities,
        audit=audit, now=lambda: NOW), session, audit


def test_reveal_returns_identity_but_audit_omits_revealed_user_id() -> None:
    service, session, audit = _service()
    result = asyncio.run(service.reveal(actor=_actor(), target_type="post",
        target_id=TARGET_ID, reason="安全调查", request_id="rid"))
    assert result.author_user_id == AUTHOR_ID and result.username == "student01"
    data = audit.record_success.call_args.kwargs["after_data"]
    assert "author_user_id" not in data and str(AUTHOR_ID) not in str(data)
    session.flush.assert_awaited_once()


def test_missing_or_nonanonymous_target_commits_failure_audit_then_raises() -> None:
    service, session, audit = _service(author_id=None, identity=False)
    with pytest.raises(AnonymousIdentityNotFound):
        asyncio.run(service.reveal(actor=_actor(), target_type="comment",
            target_id=TARGET_ID, reason="安全调查", request_id="rid"))
    audit.record_failure.assert_called_once()
    assert audit.record_failure.call_args.kwargs["error_code"] == "ANONYMOUS_IDENTITY_NOT_FOUND"
    session.flush.assert_awaited_once()


def test_historical_identity_adapter_does_not_filter_deleted_users() -> None:
    session = MagicMock(); result = MagicMock(); result.one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    asyncio.run(PlatformHistoricalIdentityAdapter(session).get(AUTHOR_ID))
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "deleted_at" not in sql and "password" not in sql.lower()
    assert "username" in sql and "display_name" in sql


def _client(service, *, user=True, permissions=("community:anonymous_identity:read",)):
    app = create_app()
    async def service_override(): return service
    app.dependency_overrides[get_anonymous_identity_service] = service_override
    if user:
        async def user_override(): return _actor(permissions)
        app.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(app)


def test_reveal_route_is_no_store_strict_and_special_permission_only() -> None:
    service = MagicMock(); service.reveal = AsyncMock(return_value=AnonymousIdentityData(
        "post", TARGET_ID, AUTHOR_ID, "student01", "学生一号", "安全调查", NOW))
    payload = {"target_type": "post", "target_id": str(TARGET_ID), "reason": "安全调查"}
    response = _client(service).post("/api/v1/community/anonymous-identities/reveal", json=payload,
                                    headers={"X-Request-Id": "reveal-rid"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-Id"] == "reveal-rid"
    assert _client(service, user=False).post("/api/v1/community/anonymous-identities/reveal",
                                            json=payload).status_code == 401
    assert _client(service, permissions=("community:moderate",)).post(
        "/api/v1/community/anonymous-identities/reveal", json=payload).status_code == 403
    assert _client(service).post("/api/v1/community/anonymous-identities/reveal",
        json={**payload, "extra": "x"}).status_code == 422
    ids = [r.operation_id for r in create_app().routes
           if getattr(r, "operation_id", None) == "revealAnonymousIdentity"]
    assert ids == ["revealAnonymousIdentity"]
