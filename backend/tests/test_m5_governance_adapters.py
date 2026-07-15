import asyncio
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.tool_gateway.catalog import (
    GovernanceAuditInput,
    GovernanceAuthorizeInput,
    GovernanceCheckInput,
    KnowledgeSearchInput,
    KnowledgeSearchOutput,
    KnowledgeSearchItem,
    TOOL_CONTRACTS,
)
from app.modules.agent_platform.tool_gateway.governance_adapters import (
    GovernanceAuthorizeToolHandler,
    GovernanceCheckContentHandler,
    GovernanceWriteAuditHandler,
    M4AuditAdapter,
    M4ContentSafetyAdapter,
    M4ToolAuthorizationAdapter,
)
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry
from app.modules.platform.audit import AuditService
from app.modules.platform.moderation_scan import ScanResult


USER_ID = UUID("10000000-0000-4000-8000-000000000001")


def _context(*permissions: str) -> UserContext:
    return UserContext(
        user_id=USER_ID,
        username="student01",
        roles=("student",),
        permissions=permissions,
        request_id="request-123",
    )


class FakeModeration:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def scan(self, *, scope: str, text: str) -> ScanResult:
        self.calls.append((scope, text))
        marker = text.casefold()
        action = (
            "block" if "blocked" in marker
            else "review" if "reviewed" in marker
            else "mask" if "secret" in marker
            else "allow"
        )
        return ScanResult(
            action=action,
            risk_level={
                "allow": "low", "mask": "medium",
                "review": "high", "block": "critical",
            }[action],
            hits=(),
            policy_version="m4-test-v1",
            sanitized_text=text.replace("secret", "***"),
        )


class FakeAuditRepository:
    def __init__(self) -> None:
        self.entries = []

    def add(self, entry) -> None:
        self.entries.append(entry)


def test_content_safety_masks_nested_strings_and_revalidates_models() -> None:
    moderation = FakeModeration()
    adapter = M4ContentSafetyAdapter(moderation)  # type: ignore[arg-type]
    context = _context("knowledge:read")
    definition = TOOL_CONTRACTS["knowledge.search"].definition
    payload = KnowledgeSearchInput(
        query="secret query", filters={"nested": "secret value"}
    )

    safe = asyncio.run(adapter.check_input(context, definition, payload))

    assert isinstance(safe, KnowledgeSearchInput)
    assert safe.query == "*** query"
    assert safe.filters == {"nested": "*** value"}
    assert moderation.calls == [
        ("tool_input", "secret query"),
        ("tool_input", "secret value"),
    ]


@pytest.mark.parametrize("marker", ["reviewed text", "blocked text"])
def test_content_safety_review_and_block_use_safe_forbidden(marker: str) -> None:
    adapter = M4ContentSafetyAdapter(FakeModeration())  # type: ignore[arg-type]
    payload = KnowledgeSearchInput(query=marker)
    with pytest.raises(AppError) as error:
        asyncio.run(adapter.check_input(
            _context("knowledge:read"),
            TOOL_CONTRACTS["knowledge.search"].definition,
            payload,
        ))
    assert error.value.status_code == 403
    assert error.value.code == "TOOL_FORBIDDEN"
    assert marker not in str(error.value)


def test_content_safety_scans_and_masks_outputs() -> None:
    adapter = M4ContentSafetyAdapter(FakeModeration())  # type: ignore[arg-type]
    output = KnowledgeSearchOutput(
        items=(KnowledgeSearchItem(
            chunk_id=UUID("20000000-0000-4000-8000-000000000001"),
            document_id=UUID("20000000-0000-4000-8000-000000000002"),
            title="secret title", snippet="safe", score=0.9,
        ),),
        retrieval_version="mock-v1",
    )
    safe = asyncio.run(adapter.check_output(
        _context("knowledge:read"),
        TOOL_CONTRACTS["knowledge.search"].definition,
        output,
    ))
    assert isinstance(safe, KnowledgeSearchOutput)
    assert safe.items[0].title == "*** title"


def test_authorization_defaults_to_deny_for_permissions_allowlist_and_internal() -> None:
    adapter = M4ToolAuthorizationAdapter()
    definition = TOOL_CONTRACTS["knowledge.search"].definition
    asyncio.run(adapter.authorize(
        context=_context("knowledge:read"), definition=definition,
        agent_allowlist=(definition.name,), trusted_runtime=False,
    ))
    for context, allowlist in [
        (_context(), (definition.name,)),
        (_context("knowledge:read"), ("service.get_guide",)),
    ]:
        with pytest.raises(AppError) as error:
            asyncio.run(adapter.authorize(
                context=context, definition=definition,
                agent_allowlist=allowlist, trusted_runtime=False,
            ))
        assert error.value.code == "TOOL_FORBIDDEN"

    internal = TOOL_CONTRACTS["governance.check_content"].definition
    with pytest.raises(AppError):
        asyncio.run(adapter.authorize(
            context=_context("moderation:execute"), definition=internal,
            agent_allowlist=(internal.name,), trusted_runtime=False,
        ))


def test_governance_handlers_call_m4_services_without_executor_recursion() -> None:
    moderation = FakeModeration()
    repository = FakeAuditRepository()
    audit_service = AuditService(repository)  # type: ignore[arg-type]
    context = _context("knowledge:read", "agent:run", "audit:write")

    check = GovernanceCheckContentHandler(moderation)  # type: ignore[arg-type]
    check_result = asyncio.run(check(
        context,
        GovernanceCheckInput(text="secret", scope="agent_context"),
    ))
    assert check_result.action == "mask"
    assert check_result.sanitized_text == "***"

    authorize = GovernanceAuthorizeToolHandler(
        registry=ToolRegistry(TOOL_CONTRACTS.values()),
        authorization=M4ToolAuthorizationAdapter(),
        agent_allowlists={"knowledge_agent": ("knowledge.search",)},
    )
    allowed = asyncio.run(authorize(
        context,
        GovernanceAuthorizeInput(
            user_id=USER_ID, agent_code="knowledge_agent",
            tool_name="knowledge.search",
        ),
    ))
    denied = asyncio.run(authorize(
        context,
        GovernanceAuthorizeInput(
            user_id=UUID("10000000-0000-4000-8000-000000000002"),
            agent_code="knowledge_agent", tool_name="knowledge.search",
        ),
    ))
    assert allowed.allowed is True
    assert denied.model_dump() == {
        "allowed": False, "reason_code": "USER_CONTEXT_MISMATCH"
    }

    write = GovernanceWriteAuditHandler(audit_service)
    result = asyncio.run(write(
        context,
        GovernanceAuditInput(
            action="agent.approval.decide", request_id="request-123",
            result="denied", metadata={"token": "must-not-leak"},
        ),
    ))
    assert result.audit_id is not None
    assert repository.entries[-1].after_data["metadata"]["token"] == "***"


def test_m4_audit_adapter_records_only_safe_execution_summary() -> None:
    repository = FakeAuditRepository()
    adapter = M4AuditAdapter(AuditService(repository))  # type: ignore[arg-type]
    audit_id = asyncio.run(adapter.record(
        context=_context("knowledge:read"),
        definition=TOOL_CONTRACTS["knowledge.search"].definition,
        result="denied", duration_ms=12, error_code="TOOL_FORBIDDEN",
    ))
    entry = repository.entries[0]
    assert audit_id == entry.id
    assert entry.result == "failure"
    assert entry.error_code == "TOOL_FORBIDDEN"
    assert entry.after_data == {
        "tool_name": "knowledge.search",
        "tool_version": "1.0.0",
        "result": "denied",
        "duration_ms": 12,
    }
