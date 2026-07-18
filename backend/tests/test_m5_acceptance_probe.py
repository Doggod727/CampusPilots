from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.tool_gateway.mocks import build_mock_handlers
from app.scripts import m5_acceptance_probe as probe


def test_contract_maps_cover_all_m5_operations():
    declared = probe.contract_operations()
    implemented = probe.implemented_operations()
    assert len(declared) == 31
    for operation_id, method_path in declared.items():
        assert implemented.get(operation_id) == method_path


def test_tool_fingerprint_is_deterministic_and_sensitive_to_contract():
    first = probe.tool_fingerprint("knowledge.search")
    assert first == probe.tool_fingerprint("knowledge.search")
    assert len(first) == 64
    others = {probe.tool_fingerprint(name) for name in TOOL_CONTRACTS if name != "knowledge.search"}
    assert first not in others
    assert len(others) == len(TOOL_CONTRACTS) - 1


def test_tool_fingerprint_rejects_unknown_tool():
    try:
        probe.tool_fingerprint("unknown.tool")
    except probe.ProbeFailure as exc:
        assert "TOOL_UNKNOWN" in exc.code
    else:
        raise AssertionError("unknown tool must be rejected")


def test_mock_classification_distinguishes_real_handlers():
    mocks = build_mock_handlers()
    assert mocks
    for handler in mocks.values():
        assert probe.is_mock_handler(handler)
    assert not probe.is_mock_handler(object())
