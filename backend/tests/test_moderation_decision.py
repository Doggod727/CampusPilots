import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyDecision, IdempotencyReplay
from app.modules.platform.models import ModerationCase
from app.modules.platform.moderation_decision import (
    ModerationCaseAlreadyDecided,
    ModerationCaseNotFound,
    ModerationDecisionService,
    ResourceVersionConflict,
)
from app.modules.platform.moderation_handlers import ModerationHandlerRegistry
from app.modules.platform.repositories import ModerationCaseRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="community_operator", name="运营员"),),
        permissions=("moderation:decide",), last_login_at=None, created_at=NOW, version=1,
    )


def _case() -> ModerationCase:
    return ModerationCase(
        id=uuid4(), target_module="community", target_type="post", target_id=uuid4(),
        content_excerpt="摘要", risk_level="high", rule_hits=[], status="pending",
        version=1, created_at=NOW, updated_at=NOW,
    )


class _Handler:
    def __init__(self):
        self.approve = AsyncMock()
        self.reject = AsyncMock()
        self.escalate = AsyncMock()


def _service(case: ModerationCase | None):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    repository = MagicMock(spec=ModerationCaseRepository)
    repository.get_by_id_for_update = AsyncMock(return_value=case)
    repository.decide_if_version = AsyncMock(return_value=True)
    idempotency = MagicMock()
    idempotency.begin = AsyncMock(return_value=IdempotencyDecision(record_id=uuid4()))
    idempotency.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    registry = ModerationHandlerRegistry()
    handler = _Handler()
    registry.register(target_module="community", target_type="post", handler=handler)
    service = ModerationDecisionService(
        session=session, repository=repository, idempotency_service=idempotency,
        audit_service=audit, handlers=registry, now=lambda: NOW,
    )
    return service, repository, idempotency, audit, handler


def test_decision_service_calls_handler_updates_case_audits_and_completes_idempotency() -> None:
    case = _case()
    service, repository, idempotency, audit, handler = _service(case)
    actor = _actor()

    result = asyncio.run(service.decide(
        actor=actor, case_id=case.id, decision="approved", reason="通过",
        expected_version=1, idempotency_key="idem-123456", request_id="req",
        request_body={"decision": "approved", "reason": "通过", "version": 1},
    ))

    assert result.status_code == 200
    assert result.body["data"]["status"] == "approved"
    handler.approve.assert_awaited_once()
    repository.decide_if_version.assert_awaited_once()
    idempotency.complete.assert_awaited_once()
    audit.record_success.assert_called_once()
    assert "token" not in repr(result)
    assert service._session.commit.call_count == 0


def test_decision_service_rejects_missing_terminal_or_stale_cases() -> None:
    service, _, _, _, _ = _service(None)
    with pytest.raises(ModerationCaseNotFound):
        asyncio.run(service.decide(
            actor=_actor(), case_id=uuid4(), decision="rejected", reason="不通过",
            expected_version=1, idempotency_key="idem-123456", request_id="req", request_body={},
        ))
    case = _case(); case.status = "approved"
    service, _, _, _, _ = _service(case)
    with pytest.raises(ModerationCaseAlreadyDecided):
        asyncio.run(service.decide(
            actor=_actor(), case_id=case.id, decision="rejected", reason="重复",
            expected_version=1, idempotency_key="idem-123456", request_id="req", request_body={},
        ))
    case = _case()
    service, repository, _, _, _ = _service(case)
    with pytest.raises(ResourceVersionConflict):
        asyncio.run(service.decide(
            actor=_actor(), case_id=case.id, decision="rejected", reason="冲突",
            expected_version=2, idempotency_key="idem-123456", request_id="req", request_body={},
        ))
    repository.decide_if_version.assert_not_called()


def test_decision_service_pending_idempotency_is_safe_conflict() -> None:
    case = _case()
    service, _, idempotency, _, _ = _service(case)
    idempotency.begin = AsyncMock(return_value=IdempotencyDecision(record_id=uuid4(), pending=True))
    with pytest.raises(IdempotencyConflict):
        asyncio.run(service.decide(
            actor=_actor(), case_id=case.id, decision="approved", reason="通过",
            expected_version=1, idempotency_key="idem-123456", request_id="req", request_body={},
        ))
