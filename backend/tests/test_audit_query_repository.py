import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.platform.audit_schemas import audit_log_data
from app.modules.platform.models import AuditLog
from app.modules.platform.repositories import AuditLogRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class _Rows:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values


def _log() -> AuditLog:
    return AuditLog(
        id=uuid4(), actor_user_id=uuid4(), actor_username="admin01",
        action="auth.login", resource_type="user", resource_id="id",
        result="success", request_id="request-id", before_data={"token": "secret"},
        after_data={"nested": {"password": "secret"}}, created_at=NOW,
    )


def test_audit_repository_lists_filters_and_does_not_manage_session() -> None:
    log = _log()
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_Rows(1), _Rows([log])])
    repository = AuditLogRepository(session)
    items, total = asyncio.run(repository.list_page(
        page=2, page_size=10, actor_user_id=log.actor_user_id,
        action="auth.login", resource_type="user", request_id="request-id",
        from_time=NOW - timedelta(days=1), to_time=NOW,
    ))
    assert items == [log]
    assert total == 1
    sql = str(session.execute.call_args_list[1].args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert "platform.audit_logs.action = 'auth.login'" in sql
    assert "OFFSET 10" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_audit_dto_redacts_legacy_sensitive_snapshots() -> None:
    data = audit_log_data(_log())
    assert data.before_data == {"token": "***"}
    assert data.after_data == {"nested": {"password": "***"}}
    assert "secret" not in repr(data)
