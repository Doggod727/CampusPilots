import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.agent_platform.model_registry import (
    ModelArtifactInvalid,
    ModelRegisterRequest,
    ModelService,
    ModelStateConflict,
)
from app.modules.agent_platform.models import ModelVersion
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision

NOW = datetime(2026, 7, 18, tzinfo=UTC)
USER = uuid4()
MID = uuid4()
JOB = uuid4()
HASH = "a" * 64


class Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def actor():
    return AuthenticatedUser(USER, "model01", "Model", None, None, "active", (AuthenticatedRole(uuid4(), "model_engineer", "Model"),), (), None, NOW, 1)


def local_payload(**overrides):
    values = {
        "name": "campus-lora", "purpose": "agent_router", "provider": "local",
        "base_model": "tiny/model", "version": "v1",
        "artifact_key": f"artifacts/{JOB}/adapter_model.safetensors", "artifact_sha256": HASH,
        "training_job_id": JOB, "config": {},
    }
    values.update(overrides)
    return ModelRegisterRequest(**values)


def service(*, training_job=None, begin_side_effect=None):
    session = MagicMock()
    if begin_side_effect is not None:
        session.begin = MagicMock(side_effect=begin_side_effect)
    else:
        session.begin.return_value = Tx()
    repo = MagicMock()
    repo.duplicate = AsyncMock(return_value=False)
    repo.add = MagicMock()
    repo.training_job = AsyncMock(return_value=training_job)
    repo.get = AsyncMock()
    repo.evaluated = AsyncMock(return_value=True)
    repo.deactivate_purpose = AsyncMock()
    idem = MagicMock()
    idem.begin = AsyncMock(return_value=IdempotencyDecision(record_id=uuid4()))
    idem.complete = AsyncMock(return_value=True)
    svc = ModelService(session, repo, idem, MagicMock(), MagicMock(), now=lambda: NOW)
    return svc, repo


def succeeded_job(artifact_key: str | None = None):
    return MagicMock(status="succeeded", artifact_key=artifact_key or f"artifacts/{JOB}/adapter_model.safetensors")


def test_register_rejects_unknown_training_job():
    svc, _ = service(training_job=None)
    with pytest.raises(ModelArtifactInvalid):
        asyncio.run(svc.register(actor(), local_payload(), "key", "req"))


def test_register_rejects_unfinished_training_job():
    svc, _ = service(training_job=MagicMock(status="training", artifact_key=f"artifacts/{JOB}/adapter_model.safetensors"))
    with pytest.raises(ModelArtifactInvalid):
        asyncio.run(svc.register(actor(), local_payload(), "key", "req"))


def test_register_rejects_artifact_outside_training_job():
    svc, _ = service(training_job=succeeded_job(artifact_key=f"artifacts/{JOB}/other.safetensors"))
    with pytest.raises(ModelArtifactInvalid):
        asyncio.run(svc.register(actor(), local_payload(), "key", "req"))


def test_register_accepts_owned_artifact():
    with patch("app.modules.agent_platform.model_registry.verify_artifact", new=AsyncMock()):
        svc, repo = service(training_job=succeeded_job())
        status, _body, _rid = asyncio.run(svc.register(actor(), local_payload(), "key", "req"))
    assert status == 201
    assert repo.add.called


def test_register_without_training_job_skips_ownership():
    with patch("app.modules.agent_platform.model_registry.verify_artifact", new=AsyncMock()):
        svc, repo = service(training_job=None)
        payload = local_payload(training_job_id=None, artifact_key="models/manual.safetensors")
        status, _body, _rid = asyncio.run(svc.register(actor(), payload, "key", "req"))
    assert status == 201
    repo.training_job.assert_not_called()


def test_activation_race_maps_unique_index_to_stable_conflict():
    def begin_raiser():
        raise IntegrityError("INSERT INTO model_versions", {}, Exception('duplicate key value violates unique constraint "uq_model_one_active_purpose"'))

    svc, _ = service(begin_side_effect=begin_raiser)
    model = ModelVersion(
        id=MID, name="router", purpose="agent_router", provider="local", base_model="Qwen",
        version="1", quantization=None, artifact_key="models/a", artifact_sha256=HASH,
        config={}, metrics={}, status="candidate", created_by=USER, created_at=NOW, activated_at=None,
    )
    svc.r.get = AsyncMock(return_value=model)
    with pytest.raises(ModelStateConflict):
        asyncio.run(svc.change(actor(), MID, "activate", "key", "req"))
