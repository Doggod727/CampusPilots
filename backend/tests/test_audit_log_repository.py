from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import AuditLog
from app.modules.platform.repositories import AuditLogRepository


def test_add_attaches_audit_log_without_mutating_session_lifecycle() -> None:
    session = AsyncMock(spec=AsyncSession)
    audit_log = AuditLog(
        actor_user_id=uuid4(),
        actor_username="student01",
        action="auth.login",
        resource_type="user",
        resource_id="student01",
        result="success",
        request_id="request-id-123",
    )

    AuditLogRepository(session).add(audit_log)

    session.add.assert_called_once_with(audit_log)
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()
