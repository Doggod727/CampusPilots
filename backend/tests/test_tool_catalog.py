import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.agent_platform.tool_gateway.catalog import (
    ElectricityTopupInput,
    LostFoundPublishInput,
    TOOL_CONTRACTS,
    WorkOrderCreateInput,
)

EXPECTED_SCHEMA_HASHES = {
    "electricity.create_topup_request": "f005ecdcae0db0b7f78649afb38677845b40c1b42b4d0a966bf4bf7f3abf9c5d",
    "electricity.get_balance": "4ea8a62594670530e1179dc02181b78167de5350e0b8c12cb093521aaeea1aeb",
    "event.register": "e7991304a0533c447f1b0ab7a265733eb0402c78f97fa7932054d97eadb4c20b",
    "event.search": "434ebc5d31bd9dbd8322a0a2361db9733575feb6d34295691b7fae1aab4dd2ca",
    "governance.authorize_tool": "850db309cd0f0d544a9f44e4109fd295f92b559984586fe175135426e01d7feb",
    "governance.check_content": "e821d3a140729c97a46785003d058e8ed8f6fcd277641240d2127feabc25f474",
    "governance.write_audit": "cd80fe88fb0eecc4985452b5ad225487ebc07e712195038060d4eab41a8e7466",
    "knowledge.answer": "e0a152895f4be8310bf2335e5ec03a57176b3af0ee28d0d818d8905ca0594e7d",
    "knowledge.search": "6c0493ffcc3b1dab3e7bcd68018a260e1584afb7ab11e4e79ec08e335ceecab3",
    "lost_found.publish": "674a8a41eb9a222a3bc8cce1a93086953c045fb3a56e43db64f5b92c2a833da3",
    "lost_found.search_matches": "d6c7d15943f52419fd45c85a4737ccd05d3409f481a85a09e6adb913a6f05f88",
    "service.get_guide": "dd09e1741bd7bf51ca88af93795f9dd994530b65d4b6752c31b80470179f036b",
    "work_order.create": "fd358a2fd74c374fab83c11714bbf20826af5fb450951d3142dd3a6e06e1ed2b",
    "work_order.get": "dfdaa2328b73a6adb15876e3503f6787741833fe20f7ab6314e8efe59443b208",
}


def _schema_hash(name: str) -> str:
    definition = TOOL_CONTRACTS[name].definition
    payload = json.dumps(
        {"input": definition.input_schema, "output": definition.output_schema},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def test_all_initial_tool_contracts_are_frozen() -> None:
    assert len(TOOL_CONTRACTS) == 14
    assert {name: _schema_hash(name) for name in sorted(TOOL_CONTRACTS)} == EXPECTED_SCHEMA_HASHES


def test_catalog_metadata_matches_detailed_design() -> None:
    expected_timeouts = {
        "knowledge.search": 5000, "knowledge.answer": 60000,
        "service.get_guide": 3000, "work_order.create": 10000,
        "work_order.get": 3000, "electricity.get_balance": 5000,
        "electricity.create_topup_request": 10000, "event.search": 3000,
        "event.register": 10000, "lost_found.publish": 10000,
        "lost_found.search_matches": 5000, "governance.check_content": 2000,
        "governance.authorize_tool": 1000, "governance.write_audit": 2000,
    }
    assert {
        name: contract.definition.timeout_ms
        for name, contract in TOOL_CONTRACTS.items()
    } == expected_timeouts
    assert all(contract.definition.idempotent for contract in TOOL_CONTRACTS.values())
    assert {
        name for name, contract in TOOL_CONTRACTS.items()
        if contract.definition.requires_approval
    } == {
        "work_order.create", "electricity.create_topup_request",
        "event.register", "lost_found.publish",
    }
    audit = TOOL_CONTRACTS["governance.write_audit"].definition
    assert audit.risk_level == "r2"
    assert audit.visibility == "runtime_internal"
    assert audit.requires_approval is False


def test_write_tool_inputs_enforce_detailed_design_fields() -> None:
    with pytest.raises(ValidationError):
        WorkOrderCreateInput.model_validate({
            "room_id": "00000000-0000-4000-8000-000000000001",
            "fault_type": "water", "description": "too short",
            "unexpected": True,
        })
    with pytest.raises(ValidationError):
        ElectricityTopupInput.model_validate({
            "room_id": "00000000-0000-4000-8000-000000000001",
            "amount": "500.01",
        })
    with pytest.raises(ValidationError):
        LostFoundPublishInput.model_validate({
            "item_type": "lost", "title": "Card", "category": "card",
            "location": "library", "occurred_at": "2026-07-15T08:00:00Z",
            "description": "lost", "contact_preference": "phone",
        })


def test_sql_seed_uses_the_frozen_contract_names_and_metadata() -> None:
    sql_path = Path(__file__).parents[2] / "docx" / "deliverables" / "sql" / "013_agent_platform_seed.sql"
    sql = sql_path.read_text(encoding="utf-8")

    for name in TOOL_CONTRACTS:
        assert f"('{name}'" in sql
    assert '"room_id","fault_type","description"' in sql
    assert '"topup_request_id","status","amount","notice"' in sql
    assert '"item_type","title","category","location","occurred_at","description"' in sql
    assert "'[\"knowledge:read\"]'::jsonb, 5000, true, false" in sql
    assert "'[\"audit:write\"]'::jsonb, 2000, true, false" in sql
    assert "'work_order.get', 'm2', '查询本人可见的报修工单', 'r1'" in sql
