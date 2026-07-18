from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.scripts.runtime_checkpoint_probe import (
    ProbeFailure,
    ProbeManifest,
    _probe_uuid,
    validate_tag,
)


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
TAG = "20260717t080000z-abcd1234"


def manifest() -> ProbeManifest:
    return ProbeManifest(
        tag=TAG,
        run_id=_probe_uuid(TAG,"run"),
        step_id=_probe_uuid(TAG,"step"),
        tool_call_id=_probe_uuid(TAG,"tool-call"),
        approval_id=_probe_uuid(TAG,"approval"),
        room_id=_probe_uuid(TAG,"room"),
        request_id=f"rtcp-{TAG}",
        balance_before=Decimal("42.00"),
        created_at=NOW,
    )


def test_checkpoint_probe_manifest_round_trips_without_secrets() -> None:
    original=manifest(); payload=original.to_json(); restored=ProbeManifest.from_json(payload)
    assert restored==original
    serialized=str(payload).lower()
    assert "password" not in serialized and "secret" not in serialized and "ciphertext" not in serialized
    assert restored.idempotency_key not in serialized


def test_checkpoint_probe_manifest_and_tag_are_fail_closed() -> None:
    with pytest.raises(ProbeFailure,match="CHECKPOINT_PROBE_TAG_INVALID"):
        validate_tag("../../unsafe")
    payload=manifest().to_json(); payload["run_id"]=str(_probe_uuid(TAG,"other"))
    with pytest.raises(ProbeFailure,match="CHECKPOINT_PROBE_MANIFEST_INVALID"):
        ProbeManifest.from_json(payload)


def test_checkpoint_probe_powershell_requires_ack_and_real_sixty_second_lease() -> None:
    script=(Path(__file__).resolve().parents[2]/"scripts"/"verify-runtime-checkpoint-recovery.ps1").read_text(encoding="utf-8")
    assert "IUnderstandThisCreatesSyntheticDatabaseRecords" in script
    assert 'AGENT_RUNTIME_CLAIM_TIMEOUT_SECONDS = "60"' in script
    assert "Get-Content .env" not in script and "Start-Transcript" not in script
