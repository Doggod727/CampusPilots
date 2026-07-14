import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.config_service import (
    ConfigNotEditable,
    ConfigNotFound,
    ConfigService,
    ConfigVersionConflict,
    InvalidConfigValue,
)
from app.modules.platform.models import AppConfig
from app.modules.platform.repositories import ConfigRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=("config:write",), last_login_at=None, created_at=NOW, version=1,
    )


def _config(*, editable: bool = True, value_type: str = "integer") -> AppConfig:
    return AppConfig(key="demo.limit", namespace="demo", value=1, value_type=value_type,
                     description="demo", editable=editable, version=1, created_at=NOW, updated_at=NOW)


def _service(config: AppConfig | None):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    repository = MagicMock(spec=ConfigRepository)
    repository.get_by_key = AsyncMock(return_value=config)
    repository.update_if_version = AsyncMock(return_value=True)
    audit = MagicMock()
    service = ConfigService(session=session, repository=repository, audit_service=audit, now=lambda: NOW)
    return service, session, repository, audit


def test_config_service_updates_typed_value_and_audits() -> None:
    config = _config()
    service, session, repository, audit = _service(config)
    result = asyncio.run(service.update(actor=_actor(), key=config.key, value=5, expected_version=1, request_id="req"))
    assert result.value == 5
    assert result.version == 2
    repository.update_if_version.assert_awaited_once()
    audit.record_success.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_config_service_rejects_missing_noneditable_wrong_type_and_version() -> None:
    service, _, _, _ = _service(None)
    with pytest.raises(ConfigNotFound):
        asyncio.run(service.update(actor=_actor(), key="demo.limit", value=2, expected_version=1, request_id="req"))
    service, _, _, _ = _service(_config(editable=False))
    with pytest.raises(ConfigNotEditable):
        asyncio.run(service.update(actor=_actor(), key="demo.limit", value=2, expected_version=1, request_id="req"))
    service, _, _, _ = _service(_config())
    with pytest.raises(InvalidConfigValue):
        asyncio.run(service.update(actor=_actor(), key="demo.limit", value="two", expected_version=1, request_id="req"))
    service, _, _, _ = _service(_config())
    with pytest.raises(ConfigVersionConflict):
        asyncio.run(service.update(actor=_actor(), key="demo.limit", value=2, expected_version=2, request_id="req"))
