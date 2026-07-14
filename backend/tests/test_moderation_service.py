import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.moderation import InvalidModerationTarget, ModerationService
from app.modules.platform.moderation_scan import ScanHit, ScanResult
from app.modules.platform.repositories import ModerationCaseRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _result(action: str) -> ScanResult:
    return ScanResult(
        action=action, risk_level="high" if action == "review" else "low",
        hits=(ScanHit(rule="rule-id", action=action),),
        policy_version="m4-sensitive-v1", sanitized_text="safe",
    )


def test_moderation_service_submits_only_review_or_block_and_saves_safe_excerpt() -> None:
    repository = MagicMock(spec=ModerationCaseRepository)
    audit = MagicMock()
    session = MagicMock()
    service = ModerationService(
        session=session, scanner=MagicMock(), repository=repository,
        audit_service=audit, now=lambda: NOW,
    )
    target_id = uuid4()
    case = asyncio.run(service.submit_case(
        result=_result("review"), target_module="community", target_type="post",
        target_id=target_id, content="x" * 600, submitted_by=uuid4(), actor=None,
        request_id="request-id",
    ))
    assert case is not None
    assert len(case.content_excerpt) == 500
    assert case.status == "pending"
    assert case.rule_hits == [{"rule": "rule-id", "action": "review"}]
    repository.add.assert_called_once_with(case)
    audit.record_success.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_moderation_service_does_not_create_case_for_allow_or_mask() -> None:
    repository = MagicMock(spec=ModerationCaseRepository)
    service = ModerationService(
        session=MagicMock(), scanner=MagicMock(), repository=repository,
        audit_service=MagicMock(), now=lambda: NOW,
    )
    result = asyncio.run(service.submit_case(
        result=_result("allow"), target_module="community", target_type="post",
        target_id=uuid4(), content="safe", submitted_by=None, actor=None,
        request_id="request-id",
    ))
    assert result is None
    repository.add.assert_not_called()


def test_moderation_service_rejects_unknown_target_module_without_persistence() -> None:
    repository = MagicMock(spec=ModerationCaseRepository)
    service = ModerationService(
        session=MagicMock(), scanner=MagicMock(), repository=repository,
        audit_service=MagicMock(), now=lambda: NOW,
    )
    with pytest.raises(InvalidModerationTarget):
        asyncio.run(service.submit_case(
            result=_result("review"), target_module="unknown", target_type="post",
            target_id=uuid4(), content="text", submitted_by=None, actor=None,
            request_id="request-id",
        ))
    repository.add.assert_not_called()
