from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.agent_platform.models import AgentRun, AgentRunEvent, AgentRuntimeCommand
from app.scripts.runtime_outbox_probe import (
    PROBE_INPUT,
    ProbeFailure,
    ProbeManifest,
    ProbeSnapshot,
    validate_snapshot,
    validate_tag,
)
from app.scripts.runtime_worker import resolve_worker_id


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def _snapshot() -> tuple[ProbeManifest, ProbeSnapshot, tuple[str, str]]:
    run_ids = (uuid4(), uuid4())
    manifest = ProbeManifest(tag="20260717t080000z-abcd1234", run_ids=run_ids, created_at=NOW)
    workers = ("runtime-probe-a", "runtime-probe-b")
    runs = tuple(
        AgentRun(
            id=run_id,
            client_request_id=f"{manifest.request_prefix}{index:03d}",
            input_summary=PROBE_INPUT,
            status="cancelled",
            finish_reason="user_cancelled",
            finished_at=NOW + timedelta(seconds=3),
            step_count=0,
        )
        for index, run_id in enumerate(run_ids, start=1)
    )
    commands = (
        AgentRuntimeCommand(
            id=uuid4(),
            run_id=run_ids[0],
            action="cancel",
            status="succeeded",
            attempt_count=1,
            claimed_by=workers[0],
            claimed_at=NOW,
            completed_at=NOW + timedelta(seconds=2),
            error_code=None,
        ),
        AgentRuntimeCommand(
            id=uuid4(),
            run_id=run_ids[1],
            action="cancel",
            status="succeeded",
            attempt_count=1,
            claimed_by=workers[1],
            claimed_at=NOW + timedelta(seconds=1),
            completed_at=NOW + timedelta(seconds=3),
            error_code=None,
        ),
    )
    events = tuple(
        AgentRunEvent(
            id=uuid4(),
            run_id=run_id,
            sequence=1,
            event="done",
            data={"status": "cancelled"},
        )
        for run_id in run_ids
    )
    return manifest, ProbeSnapshot(runs, commands, events, (), (), ()), workers


def test_probe_accepts_two_overlapping_workers_and_exactly_once_events() -> None:
    manifest, snapshot, workers = _snapshot()
    summary, problems = validate_snapshot(manifest, snapshot, workers)
    assert problems == ()
    assert summary["worker_claims"] == {workers[0]: 1, workers[1]: 1}
    assert summary["workers_overlapped"] is True
    assert summary["active_command_count"] == 0
    assert summary["step_count"] == summary["tool_call_count"] == 0


def test_probe_rejects_wrong_owner_duplicate_command_and_event() -> None:
    manifest, snapshot, workers = _snapshot()
    bad_command = AgentRuntimeCommand(
        id=uuid4(),
        run_id=manifest.run_ids[0],
        action="cancel",
        status="succeeded",
        attempt_count=1,
        claimed_by="untrusted-worker",
        claimed_at=NOW,
        completed_at=NOW,
    )
    duplicate_event = AgentRunEvent(
        id=uuid4(),
        run_id=manifest.run_ids[0],
        sequence=1,
        event="done",
        data={"status": "cancelled"},
    )
    changed = ProbeSnapshot(
        snapshot.runs,
        snapshot.commands + (bad_command,),
        snapshot.events + (duplicate_event,),
        (),
        (),
        (),
    )
    _, problems = validate_snapshot(manifest, changed, workers)
    assert "COMMAND_CARDINALITY_INVALID" in problems
    assert "EVENT_CARDINALITY_INVALID" in problems
    assert "WORKER_DISTRIBUTION_INVALID" in problems


def test_probe_manifest_and_tag_validation_are_fail_closed() -> None:
    with pytest.raises(ProbeFailure, match="RUNTIME_PROBE_TAG_INVALID"):
        validate_tag("../../unsafe")
    with pytest.raises(ProbeFailure, match="RUNTIME_PROBE_MANIFEST_INVALID"):
        ProbeManifest.from_json({"tag": "valid-tag", "run_ids": []})


def test_runtime_worker_id_is_explicit_or_process_unique(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_WORKER_ID", " worker-a ")
    assert resolve_worker_id() == "worker-a"
    monkeypatch.delenv("AGENT_RUNTIME_WORKER_ID")
    monkeypatch.setattr("app.scripts.runtime_worker.socket.gethostname", lambda: "host-a")
    monkeypatch.setattr("app.scripts.runtime_worker.os.getpid", lambda: 4321)
    assert resolve_worker_id() == "host-a:4321"
