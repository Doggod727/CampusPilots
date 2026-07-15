import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from pydantic import TypeAdapter

from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import ScanHit, ScanResult
from app.modules.platform.moderation_schemas import TargetModule
from app.modules.platform.models import ModerationCase, SensitiveWord
from app.modules.platform.repositories import ModerationCaseRepository
from app.modules.platform.sensitive_word_schemas import SensitiveScope


def _constraint_sql(model: type, name: str) -> str:
    constraint = next(
        item for item in model.__table__.constraints if item.name == name
    )
    return str(constraint.sqltext)


def test_platform_orm_constraints_include_m5_compatibility_values() -> None:
    sensitive_scope = _constraint_sql(SensitiveWord, "ck_sensitive_words_scope")
    target_module = _constraint_sql(
        ModerationCase, "ck_moderation_cases_target_module"
    )

    for value in ("tool_input", "tool_output", "agent_context"):
        assert value in sensitive_scope
    assert "agent_platform" in target_module


def test_platform_pydantic_types_accept_old_and_m5_values() -> None:
    scope_adapter = TypeAdapter(SensitiveScope)
    target_adapter = TypeAdapter(TargetModule)

    for value in ("user_input", "ai_output", "community", "all"):
        assert scope_adapter.validate_python(value) == value
    for value in ("tool_input", "tool_output", "agent_context"):
        assert scope_adapter.validate_python(value) == value
    for value in ("ai_knowledge", "campus_service", "community", "agent_platform"):
        assert target_adapter.validate_python(value) == value


def test_moderation_service_accepts_agent_platform_target() -> None:
    repository = MagicMock(spec=ModerationCaseRepository)
    service = ModerationService(
        session=MagicMock(),
        scanner=MagicMock(),
        repository=repository,
        audit_service=MagicMock(),
        now=lambda: datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
    )
    result = ScanResult(
        action="review",
        risk_level="high",
        hits=(ScanHit(rule="rule-id", action="review"),),
        policy_version="m5-sensitive-v1",
        sanitized_text="safe",
    )

    case = asyncio.run(
        service.submit_case(
            result=result,
            target_module="agent_platform",
            target_type="tool_call",
            target_id=uuid4(),
            content="safe excerpt",
            submitted_by=None,
            actor=None,
            request_id="request-id",
        )
    )

    assert case is not None
    assert case.target_module == "agent_platform"
    repository.add.assert_called_once_with(case)
