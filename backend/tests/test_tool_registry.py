from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.tool_gateway.errors import (
    DuplicateToolRegistration,
    ToolDisabled,
    ToolNotFound,
)
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry


def _context(*permissions: str) -> UserContext:
    return UserContext(
        user_id=uuid4(), username="student01", permissions=permissions,
        roles=("student",), request_id="request-123",
    )


def test_registry_registers_and_resolves_all_frozen_tools() -> None:
    registry = ToolRegistry(TOOL_CONTRACTS.values())
    assert registry.resolve("knowledge.search").definition.version == "1.0.0"
    assert registry.resolve("knowledge.search", "1.0.0").definition.name == "knowledge.search"
    with pytest.raises(ToolNotFound) as missing:
        registry.resolve("unknown.tool")
    assert missing.value.code == "TOOL_NOT_FOUND"


def test_registry_rejects_duplicate_name_and_version() -> None:
    contract = TOOL_CONTRACTS["knowledge.search"]
    registry = ToolRegistry([contract])
    with pytest.raises(DuplicateToolRegistration):
        registry.register(contract)


def test_default_resolution_uses_highest_enabled_semantic_version() -> None:
    old = TOOL_CONTRACTS["knowledge.search"]
    newer = replace(
        old,
        definition=old.definition.model_copy(update={"version": "1.2.0"}),
    )
    disabled = replace(
        old,
        definition=old.definition.model_copy(update={"version": "2.0.0", "enabled": False}),
    )
    registry = ToolRegistry([old, newer, disabled])
    assert registry.resolve("knowledge.search").definition.version == "1.2.0"
    with pytest.raises(ToolDisabled):
        registry.resolve("knowledge.search", "2.0.0")


def test_list_allowed_applies_permissions_allowlist_visibility_and_sorting() -> None:
    registry = ToolRegistry(TOOL_CONTRACTS.values())
    context = _context("knowledge:read", "service:read", "moderation:execute")
    visible = registry.list_allowed(
        context,
        ["service.get_guide", "governance.check_content", "knowledge.answer", "knowledge.search"],
    )
    assert tuple(item.definition.name for item in visible) == (
        "knowledge.answer", "knowledge.search", "service.get_guide"
    )
    internal = registry.list_allowed(
        context, ["governance.check_content"], visibility="runtime_internal"
    )
    assert tuple(item.definition.name for item in internal) == ("governance.check_content",)


def test_empty_permissions_and_unknown_allowlist_default_to_deny() -> None:
    registry = ToolRegistry(TOOL_CONTRACTS.values())
    assert registry.list_allowed(_context(), TOOL_CONTRACTS) == ()
    assert registry.list_allowed(_context("knowledge:read"), ["unknown.tool"]) == ()
