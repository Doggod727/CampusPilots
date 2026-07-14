import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.models import SensitiveWord
from app.modules.platform.sensitive_word_routes import get_repository, get_service
from app.modules.platform.sensitive_words import (
    DuplicateSensitiveWord,
    SensitiveWordService,
)
from app.modules.platform.repositories import SensitiveWordRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _actor(*permissions: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def _rule() -> SensitiveWord:
    return SensitiveWord(
        id=uuid4(), word="秘密", match_type="contains", action="mask",
        replacement="***", scope="user_input", enabled=True,
        created_at=NOW, updated_at=NOW,
    )


def test_sensitive_word_service_creates_and_deletes_in_caller_transaction() -> None:
    session = MagicMock()
    session.flush = AsyncMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    repository = MagicMock(spec=SensitiveWordRepository)
    repository.get_by_rule = AsyncMock(return_value=None)
    repository.get_by_id = AsyncMock(return_value=_rule())
    repository.delete = AsyncMock(return_value=True)
    audit = MagicMock()
    service = SensitiveWordService(
        session=session, repository=repository, audit_service=audit, now=lambda: NOW
    )

    created = asyncio.run(service.create(
        actor=_actor("sensitive_word:write"), word="秘密", match_type="contains",
        action="mask", replacement="***", scope="user_input", enabled=True,
        request_id="request-id",
    ))
    asyncio.run(service.delete(actor=_actor("sensitive_word:write"), word_id=created.id, request_id="request-id"))

    assert created.word == "秘密"
    assert audit.record_success.call_count == 2
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_sensitive_word_routes_return_page_and_require_permissions() -> None:
    rule = _rule()
    repository = MagicMock(spec=SensitiveWordRepository)
    repository.list_page = AsyncMock(return_value=([rule], 1))
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("sensitive_word:read")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/api/v1/sensitive-words?page=1&page_size=10",
        headers={"X-Request-Id": "sensitive-list"},
    )

    assert response.status_code == 200
    assert response.json()["request_id"] == "sensitive-list"
    assert response.json()["data"]["pagination"]["total_pages"] == 1
    assert response.json()["data"]["items"][0]["word"] == "秘密"

    forbidden_app = create_app()
    forbidden_app.dependency_overrides[get_authenticated_user] = lambda: _actor("role:read")
    forbidden = TestClient(forbidden_app, raise_server_exceptions=False).get(
        "/api/v1/sensitive-words"
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "AUTH_FORBIDDEN"


def test_sensitive_word_create_route_validates_payload_and_delete_response() -> None:
    created_rule = _rule()
    service = MagicMock(spec=SensitiveWordService)
    service.create = AsyncMock(return_value=created_rule)
    service.delete = AsyncMock()
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("sensitive_word:write")
    client = TestClient(app, raise_server_exceptions=False)

    invalid = client.post(
        "/api/v1/sensitive-words",
        json={"word": "x", "match_type": "contains", "action": "mask", "scope": "all"},
    )
    assert invalid.status_code == 422

    created = client.post(
        "/api/v1/sensitive-words",
        json={"word": "秘密", "match_type": "contains", "action": "mask", "replacement": "***", "scope": "all"},
    )
    assert created.status_code == 201
    assert created.json()["data"]["id"] == str(created_rule.id)

    deleted = client.delete(f"/api/v1/sensitive-words/{uuid4()}")
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {}


def test_sensitive_word_service_rejects_duplicate_rule() -> None:
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    repository = MagicMock(spec=SensitiveWordRepository)
    repository.get_by_rule = AsyncMock(return_value=_rule())
    service = SensitiveWordService(
        session=session, repository=repository, audit_service=MagicMock(), now=lambda: NOW
    )
    with pytest.raises(DuplicateSensitiveWord):
        asyncio.run(service.create(
            actor=_actor("sensitive_word:write"), word="秘密", match_type="contains",
            action="mask", replacement="***", scope="user_input", enabled=True,
            request_id="request-id",
        ))
