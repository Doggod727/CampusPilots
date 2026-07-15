import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.modules.agent_platform.evaluations import (
    EvaluationCreateCommand,
    EvaluationDatasetNotReady,
    EvaluationNotCompleted,
    EvaluationNotFound,
    EvaluationRepository,
    EvaluationService,
    EvaluationTargetNotFound,
    evaluation_data,
)
from app.modules.agent_platform.models import DatasetVersion, EvaluationJob, EvaluationMetric
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision


NOW = datetime(2026, 7, 15, tzinfo=UTC)
USER_ID = uuid4()
TARGET_ID = uuid4()
DATASET_ID = uuid4()


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        USER_ID,
        "model01",
        "Model Engineer",
        None,
        None,
        "active",
        (AuthenticatedRole(uuid4(), "model_engineer", "Model Engineer"),),
        (),
        None,
        NOW,
        1,
    )


def command(**changes) -> EvaluationCreateCommand:
    values = {"target_type": "model", "target_id": TARGET_ID, "config": {"token": "secret", "seed": 42}}
    values.update(changes)
    return EvaluationCreateCommand(**values)


def evaluation(status="succeeded", evaluation_id=None) -> EvaluationJob:
    return EvaluationJob(
        id=evaluation_id or uuid4(),
        target_type="model",
        target_id=TARGET_ID,
        dataset_version_id=None,
        status=status,
        config={"api_key": "hidden", "seed": 42},
        summary={"accuracy": 0.9},
        report_key=None,
        error_code=None,
        created_by=USER_ID,
        started_at=NOW,
        finished_at=NOW if status == "succeeded" else None,
        created_at=NOW,
    )


def metric(evaluation_id, name="accuracy", value=0.9, slice_name="all") -> EvaluationMetric:
    return EvaluationMetric(
        id=uuid4(), evaluation_id=evaluation_id, name=name, value=value,
        unit=None, slice_name=slice_name, created_at=NOW,
    )


def service(*, target=True, dataset=True):
    session = MagicMock()
    session.begin.return_value = Transaction()
    repository = MagicMock()
    repository.target_exists = AsyncMock(return_value=target)
    repository.ready_dataset_version = AsyncMock(
        return_value=DatasetVersion(
            id=uuid4(), dataset_id=DATASET_ID, version=1, artifact_key="artifact",
            artifact_sha256="a" * 64, format="jsonl", sample_count=1,
            split_config={}, validation_status="valid", validation_report={},
            contains_sensitive_data=False, frozen_at=NOW, created_by=USER_ID, created_at=NOW,
        ) if dataset else None
    )
    repository.add = MagicMock()
    idempotency = MagicMock()
    idempotency.begin = AsyncMock(return_value=IdempotencyDecision(record_id=uuid4()))
    idempotency.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    return EvaluationService(session, repository, idempotency, audit, now=lambda: NOW), repository, audit


def test_command_requires_paired_dataset_and_catalog_target():
    with pytest.raises(ValidationError):
        command(dataset_id=DATASET_ID)
    with pytest.raises(ValidationError):
        EvaluationCreateCommand(target_type="tool", config={})


def test_create_queues_redacted_idempotent_evaluation():
    svc, repository, audit = service()
    status, body, request_id = asyncio.run(svc.create(actor(), command(), "idem", "request-101"))
    created = repository.add.call_args.args[0]
    assert status == 202 and request_id == "request-101"
    assert body["data"]["status"] == "queued"
    assert created.config == {"token": "***", "seed": 42}
    assert "secret" not in str(body)
    audit.record_success.assert_called_once()


def test_create_rejects_missing_target_and_unready_dataset():
    svc, _, _ = service(target=False)
    with pytest.raises(EvaluationTargetNotFound):
        asyncio.run(svc.create(actor(), command(), "key", "request"))
    svc, _, _ = service(dataset=False)
    with pytest.raises(EvaluationDatasetNotReady):
        asyncio.run(svc.create(actor(), command(dataset_id=DATASET_ID, dataset_version=1), "key", "request"))


def test_detail_redacts_config_and_metrics_are_stable():
    item = evaluation()
    data = evaluation_data(item, (metric(item.id),))
    assert data.config["api_key"] == "***"
    assert data.metrics[0].name == "accuracy"


def test_compare_requires_existing_successful_jobs_and_uses_slice_keys():
    first, second = evaluation(), evaluation()
    svc, repository, _ = service()
    repository.get_many = AsyncMock(return_value=(first, second))
    repository.metrics_for = AsyncMock(return_value={
        first.id: (metric(first.id), metric(first.id, "accuracy", 0.8, "freshmen")),
        second.id: (metric(second.id, value=0.95),),
    })
    result = asyncio.run(svc.compare((first.id, second.id)))
    assert result.metric_names == ("accuracy", "accuracy@freshmen")
    assert result.rows[0].metrics["accuracy@freshmen"] == 0.8

    repository.get_many = AsyncMock(return_value=(first,))
    with pytest.raises(EvaluationNotFound):
        asyncio.run(svc.compare((first.id, second.id)))
    second.status = "running"
    repository.get_many = AsyncMock(return_value=(first, second))
    with pytest.raises(EvaluationNotCompleted):
        asyncio.run(svc.compare((first.id, second.id)))


def test_repository_claim_uses_skip_locked_and_session_is_caller_owned():
    session = MagicMock()
    statements = []

    async def execute(statement):
        statements.append(statement)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=execute)
    assert asyncio.run(EvaluationRepository(session).claim(2)) == ()
    sql = str(statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE SKIP LOCKED" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()
