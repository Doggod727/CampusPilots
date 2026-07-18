import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.modules.agent_platform.models import DatasetVersion, TrainingJob
from app.modules.agent_platform.training_worker import (
    TRAINING_DATASET_INVALID,
    TRAINING_EXECUTION_FAILED,
    TRAINING_RESOURCE_UNAVAILABLE,
    LoraTrainingBackend,
    TrainingCancelled,
    TrainingOutcome,
    TrainingResourceUnavailable,
    TrainingWorker,
    parse_training_samples,
)

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def test_parse_training_samples_accepts_text_and_pairs_and_rejects_empty():
    artifact = b'{"text": "hello world"}\n{"query": "q", "positive": "p"}\nnot-json\n{"x": 1}\n'
    samples = parse_training_samples(artifact, "jsonl")
    assert samples == ("hello world", "q\np")
    with pytest.raises(ValueError, match=TRAINING_DATASET_INVALID):
        parse_training_samples(b'{"x": 1}\n', "jsonl")
    with pytest.raises(ValueError, match=TRAINING_DATASET_INVALID):
        parse_training_samples(b"a,b\n", "csv")


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def build_worker(tmp_path: Path, job: TrainingJob, backend, artifact: bytes = b'{"text": "sample one"}\n'):
    version = SimpleNamespace(artifact_key="quarantine/x.jsonl", artifact_sha256="a" * 64, format="jsonl")

    def make_session():
        session = MagicMock()
        session.begin.return_value = Transaction()

        async def get(model, key):
            if model is DatasetVersion:
                return version
            if model is TrainingJob:
                return job
            return None

        session.get = AsyncMock(side_effect=get)
        return session

    class SessionScope:
        async def __aenter__(self):
            return make_session()

        async def __aexit__(self, *_args):
            return False

    repository = MagicMock()
    repository.claim = AsyncMock(return_value=(job,))
    repository.get = AsyncMock(return_value=job)
    repository.requeue_stale = AsyncMock(return_value=0)
    datasets = SimpleNamespace(read=AsyncMock(return_value=artifact))
    with patch("app.modules.agent_platform.training_worker.TrainingRepository", return_value=repository):
        worker = TrainingWorker(
            lambda: SessionScope(),
            backend,
            datasets,
            tmp_path,
            now=lambda: NOW,
        )
        run = asyncio.run(worker.run_once())
    return worker, run, repository


def job(method: str = "lora") -> TrainingJob:
    return TrainingJob(
        id=uuid4(), dataset_version_id=uuid4(), base_model="tiny/model", method=method,
        config={"epochs": 1, "learning_rate": 0.001, "batch_size": 2}, resource_limits={},
        status="queued", progress=0, metrics={}, created_by=uuid4(), created_at=NOW, updated_at=NOW,
    )


class SuccessBackend:
    def execute_sync(self, job_arg, samples, on_progress, should_cancel):
        on_progress(50)
        return TrainingOutcome(artifact=b"adapter-bytes", artifact_sha256="b" * 64, metrics={"steps": 4})


class CancelBackend:
    def execute_sync(self, job_arg, samples, on_progress, should_cancel):
        raise TrainingCancelled()


class ResourceBackend:
    def execute_sync(self, job_arg, samples, on_progress, should_cancel):
        raise TrainingResourceUnavailable()


class FailureBackend:
    def execute_sync(self, job_arg, samples, on_progress, should_cancel):
        raise RuntimeError("private trainer crash")


def test_worker_success_persists_artifact_metrics_and_terminal_state(tmp_path):
    current = job()
    _, run_id, _ = build_worker(tmp_path, current, SuccessBackend())
    assert run_id == current.id
    assert current.status == "succeeded" and current.progress == 100
    assert current.artifact_key == f"artifacts/{current.id}/adapter_model.safetensors"
    assert current.artifact_sha256 == "b" * 64
    assert current.metrics == {"steps": 4}
    assert current.finished_at == NOW
    assert (tmp_path / "artifacts" / str(current.id) / "adapter_model.safetensors").read_bytes() == b"adapter-bytes"


def test_worker_cancel_maps_to_cancelled_state(tmp_path):
    current = job()
    build_worker(tmp_path, current, CancelBackend())
    assert current.status == "cancelled" and current.finished_at == NOW


def test_worker_resource_unavailable_maps_stable_error(tmp_path):
    current = job(method="qlora")
    build_worker(tmp_path, current, ResourceBackend())
    assert current.status == "failed" and current.error_code == TRAINING_RESOURCE_UNAVAILABLE


def test_worker_unexpected_failure_uses_stable_code_without_details(tmp_path):
    current = job()
    build_worker(tmp_path, current, FailureBackend())
    assert current.status == "failed" and current.error_code == TRAINING_EXECUTION_FAILED
    assert "private" not in (current.error_message or "")


def test_qlora_backend_fails_closed_without_cuda_or_bitsandbytes(tmp_path):
    backend = LoraTrainingBackend(tmp_path)
    with pytest.raises(TrainingResourceUnavailable):
        backend.execute_sync(job(method="qlora"), ("sample",), lambda _v: None, lambda: False)


def test_worker_requeues_stale_active_jobs_before_claiming(tmp_path):
    current = job()
    _, _, repository = build_worker(tmp_path, current, SuccessBackend())
    repository.requeue_stale.assert_awaited_once()
    assert repository.requeue_stale.await_count == 1
    repository.claim.assert_awaited_once()


def test_local_model_evaluation_requires_loadable_artifact(tmp_path):
    from app.modules.agent_platform.evaluation_providers import ModelEvaluationProvider

    provider = ModelEvaluationProvider(
        AsyncMock(return_value=SimpleNamespace(provider="local", base_model="missing/model", artifact_key="artifacts/none/adapter_model.safetensors")),
        MagicMock(),
        model_root=tmp_path,
    )
    with pytest.raises(LookupError):
        asyncio.run(provider.evaluate(SimpleNamespace(id=uuid4(), target_type="model", target_id=uuid4(), config={}, created_by=uuid4())))
