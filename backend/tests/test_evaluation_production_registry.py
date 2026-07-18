import asyncio
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import app.scripts.evaluation_worker as production_entry
from app.core.config import Settings
from app.modules.agent_platform.evaluation_worker import (
    EVALUATION_PROVIDER_UNAVAILABLE,
    TARGET_TYPES,
    EvaluationWorker,
    build_production_evaluator_registry,
)
from app.modules.agent_platform.models import EvaluationJob

NOW = datetime(2026, 7, 18, tzinfo=UTC)


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


def test_production_worker_entry_has_no_fake_evaluator_reference():
    source = inspect.getsource(production_entry)
    assert "deterministic_fake_registry" not in source
    assert "DeterministicFakeEvaluator" not in source


def test_production_registry_fails_closed_for_all_target_types_in_both_modes():
    registry = build_production_evaluator_registry(SimpleNamespace(modelops_execution_mode="disabled"))
    for target_type in TARGET_TYPES:
        with pytest.raises(LookupError) as caught:
            registry.resolve(target_type)
        assert EVALUATION_PROVIDER_UNAVAILABLE in str(caught.value)
    with pytest.raises(ValueError):
        build_production_evaluator_registry(SimpleNamespace(modelops_execution_mode="local"))


def test_production_registry_rejects_unknown_mode():
    with pytest.raises(ValueError):
        build_production_evaluator_registry(SimpleNamespace(modelops_execution_mode="fake"))


def test_worker_with_production_registry_fails_jobs_without_metrics():
    evaluation = EvaluationJob(
        id=uuid4(), target_type="model", target_id=uuid4(), dataset_version_id=None,
        status="queued", config={}, summary={}, report_key=None,
        error_code=None, created_by=uuid4(), started_at=None, finished_at=None, created_at=NOW,
    )
    session = MagicMock()
    session.begin.return_value = Transaction()
    session.flush = AsyncMock()
    repository = MagicMock()
    repository.claim = AsyncMock(return_value=(evaluation,))
    repository.add_metric = MagicMock()
    registry = build_production_evaluator_registry(SimpleNamespace(modelops_execution_mode="disabled"))
    worker = EvaluationWorker(
        lambda: SessionScope(session),
        registry,
        repository_factory=lambda _session: repository,
        now=lambda: NOW,
    )
    assert asyncio.run(worker.run_once()) == evaluation.id
    assert evaluation.status == "failed"
    assert evaluation.error_code == EVALUATION_PROVIDER_UNAVAILABLE
    assert evaluation.summary == {}
    repository.add_metric.assert_not_called()


def test_modelops_execution_mode_defaults_to_disabled():
    field = Settings.model_fields["modelops_execution_mode"]
    assert field.default == "disabled"
