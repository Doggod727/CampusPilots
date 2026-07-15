import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import AgentRegistration
from app.modules.agent_platform.orchestration.agent_registry import (
    AGENT_REGISTRATIONS,
    AgentRegistry,
)
from app.modules.agent_platform.orchestration.errors import DuplicateAgentRegistration


EXPECTED_ALLOWLISTS = {
    "supervisor": ("governance.authorize_tool", "governance.check_content", "governance.write_audit"),
    "knowledge_agent": ("governance.check_content", "governance.write_audit", "knowledge.answer", "knowledge.search"),
    "service_agent": ("electricity.create_topup_request", "electricity.get_balance", "governance.authorize_tool", "governance.write_audit", "service.get_guide", "work_order.create", "work_order.get"),
    "community_agent": ("event.register", "event.search", "governance.authorize_tool", "governance.write_audit", "lost_found.publish", "lost_found.search_matches"),
    "governance_agent": ("governance.authorize_tool", "governance.check_content", "governance.write_audit"),
    "modelops_agent": (),
}


def test_six_builtin_agents_are_active_stable_and_match_seed_allowlists() -> None:
    registry = AgentRegistry(AGENT_REGISTRATIONS)
    active = registry.list_active()
    assert [item.definition.code for item in active] == sorted(EXPECTED_ALLOWLISTS)
    assert len(active) == 6
    assert {
        item.definition.code: item.version.tool_allowlist for item in active
    } == EXPECTED_ALLOWLISTS


def test_catalog_is_safe_and_omits_prompt_and_schema() -> None:
    registry = AgentRegistry(AGENT_REGISTRATIONS)
    catalog = registry.list_catalog()
    assert [item.code for item in catalog] == sorted(EXPECTED_ALLOWLISTS)
    rendered = repr(catalog) + str([item.model_dump() for item in catalog])
    assert "system_prompt" not in rendered
    assert "output_schema" not in rendered
    internal = registry.get_active("service_agent")
    assert internal.version.system_prompt not in repr(internal)


def test_duplicate_code_version_and_second_active_version_are_rejected() -> None:
    registry = AgentRegistry([AGENT_REGISTRATIONS[0]])
    with pytest.raises(DuplicateAgentRegistration):
        registry.register(AGENT_REGISTRATIONS[0])
    current = AGENT_REGISTRATIONS[0]
    second = AgentRegistration(
        definition=current.definition,
        version=current.version.model_copy(update={"version": "1.1.0"}),
    )
    with pytest.raises(DuplicateAgentRegistration):
        registry.register(second)


def test_missing_disabled_and_no_active_version_use_stable_errors() -> None:
    registry = AgentRegistry()
    with pytest.raises(AppError) as missing:
        registry.get_active("knowledge_agent")
    assert (missing.value.status_code, missing.value.code) == (404, "AGENT_NOT_FOUND")

    disabled = AGENT_REGISTRATIONS[1].model_copy(update={
        "definition": AGENT_REGISTRATIONS[1].definition.model_copy(update={"enabled": False})
    })
    inactive = AGENT_REGISTRATIONS[2].model_copy(update={
        "version": AGENT_REGISTRATIONS[2].version.model_copy(update={"status": "inactive"})
    })
    registry = AgentRegistry([disabled, inactive])
    for code in ("knowledge_agent", "service_agent"):
        with pytest.raises(AppError) as unavailable:
            registry.get_active(code)
        assert (unavailable.value.status_code, unavailable.value.code) == (409, "AGENT_DISABLED")
    assert registry.list_active() == ()


def test_contracts_are_strict_frozen_and_normalize_tool_names() -> None:
    payload = AGENT_REGISTRATIONS[0].model_dump()
    payload["version"]["tool_allowlist"] = [
        "governance.write_audit", "governance.check_content",
        "governance.write_audit",
    ]
    registration = AgentRegistration.model_validate(payload)
    assert registration.version.tool_allowlist == (
        "governance.check_content", "governance.write_audit"
    )
    with pytest.raises(ValidationError):
        AgentRegistration.model_validate({**payload, "unexpected": True})
    with pytest.raises(ValidationError):
        AgentRegistration.model_validate({
            **payload,
            "version": {**payload["version"], "tool_allowlist": ["invalid"]},
        })
    with pytest.raises(ValidationError):
        registration.version.version = "2.0.0"  # type: ignore[misc]
