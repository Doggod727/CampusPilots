import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.evaluation_worker import (
    EVALUATION_PROVIDER_UNAVAILABLE,
    TARGET_TYPES,
    EvaluationOutcome,
    EvaluationWorker,
    EvaluatorRegistry,
)
from app.modules.agent_platform.models import EvaluationJob
from tests.fake_evaluators import DeterministicFakeEvaluator, deterministic_fake_registry


NOW = datetime(2026, 7, 15, tzinfo=UTC)


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class SessionScope:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def job(target_type="model") -> EvaluationJob:
    return EvaluationJob(
        id=uuid4(), target_type=target_type, target_id=uuid4(), dataset_version_id=None,
        status="queued", config={"token": "not-used"}, summary={}, report_key=None,
        error_code=None, created_by=uuid4(), started_at=None, finished_at=None, created_at=NOW,
    )


def worker(repository, registry):
    session = MagicMock()
    session.begin.return_value = Transaction()
    session.flush = AsyncMock()
    return EvaluationWorker(
        lambda: SessionScope(session),
        registry,
        repository_factory=lambda _session: repository,
        now=lambda: NOW,
    ), session


def test_fake_evaluator_covers_all_target_types_deterministically():
    registry = deterministic_fake_registry()
    for target_type in TARGET_TYPES:
        evaluation = job(target_type)
        first = asyncio.run(registry.resolve(target_type).evaluate(evaluation))
        second = asyncio.run(registry.resolve(target_type).evaluate(evaluation))
        assert first == second
        assert first.summary == {"mode": "deterministic_fake", "target_type": target_type}
        assert len(first.metrics) == 2


def test_registry_rejects_unknown_registration_and_missing_provider():
    with pytest.raises(ValueError):
        EvaluatorRegistry({"unknown": DeterministicFakeEvaluator()})
    with pytest.raises(LookupError):
        EvaluatorRegistry({}).resolve("model")


def test_worker_claims_and_persists_safe_success_once():
    evaluation = job()
    repository = MagicMock()
    repository.claim = AsyncMock(return_value=(evaluation,))
    repository.add_metric = MagicMock()
    service, session = worker(repository, deterministic_fake_registry())
    assert asyncio.run(service.run_once()) == evaluation.id
    assert evaluation.status == "succeeded"
    assert evaluation.summary == {"mode": "deterministic_fake", "target_type": "model"}
    assert evaluation.error_code is None and evaluation.finished_at == NOW
    assert repository.add_metric.call_count == 2
    session.flush.assert_awaited_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_worker_maps_all_provider_failures_without_exception_text():
    class FailingEvaluator:
        async def evaluate(self, _evaluation):
            raise RuntimeError("provider-secret-and-sample")

    evaluation = job()
    repository = MagicMock()
    repository.claim = AsyncMock(return_value=(evaluation,))
    service, _ = worker(repository, EvaluatorRegistry({"model": FailingEvaluator()}))
    assert asyncio.run(service.run_once()) == evaluation.id
    assert evaluation.status == "failed"
    assert evaluation.error_code == EVALUATION_PROVIDER_UNAVAILABLE
    assert evaluation.summary == {}
    assert "provider-secret" not in str(evaluation.summary)
    repository.add_metric.assert_not_called()


def test_worker_does_nothing_when_queue_is_empty():
    repository = MagicMock()
    repository.claim = AsyncMock(return_value=())
    service, session = worker(repository, deterministic_fake_registry())
    assert asyncio.run(service.run_once()) is None
    session.flush.assert_not_awaited()
    repository.add_metric.assert_not_called()
