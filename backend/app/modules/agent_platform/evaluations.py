from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.agent_platform.models import (
    AgentDefinition,
    Dataset,
    DatasetVersion,
    EvaluationJob,
    EvaluationMetric,
    ModelVersion,
    ToolDefinition,
)
from app.modules.platform.audit import AuditService, redact
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.user_schemas import PageMetaData
from app.shared.responses import SuccessResponse


class EvaluationNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="EVALUATION_NOT_FOUND", message="评估任务不存在")


class EvaluationTargetNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="EVALUATION_TARGET_NOT_FOUND", message="评估目标不存在")


class EvaluationDatasetNotReady(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="EVALUATION_DATASET_NOT_READY", message="评估数据集版本尚未就绪")


class EvaluationNotCompleted(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="EVALUATION_NOT_COMPLETED", message="评估任务尚未成功完成")


class EvaluationCreateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_type: Literal["agent", "tool", "model", "rag", "system"]
    target_id: UUID | None = None
    dataset_id: UUID | None = None
    dataset_version: int | None = Field(default=None, ge=1)
    config: dict[str, Any]

    @model_validator(mode="after")
    def validate_references(self) -> "EvaluationCreateCommand":
        if (self.dataset_id is None) != (self.dataset_version is None):
            raise ValueError("dataset_id and dataset_version must be provided together")
        if self.target_type in {"agent", "tool", "model"} and self.target_id is None:
            raise ValueError("target_id is required for catalog and model targets")
        return self


class EvaluationMetricData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    value: float
    unit: str | None
    slice_name: str


class EvaluationData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    target_type: str
    target_id: UUID | None
    status: str
    config: dict[str, Any]
    summary: dict[str, Any]
    metrics: tuple[EvaluationMetricData, ...]
    report_key: str | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class EvaluationPageData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvaluationData, ...]
    pagination: PageMetaData


class EvaluationComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_id: UUID
    metrics: dict[str, float]


class EvaluationComparisonData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_ids: tuple[UUID, ...]
    metric_names: tuple[str, ...]
    rows: tuple[EvaluationComparisonRow, ...]


class EvaluationRepository:
    """Caller-owned persistence for evaluation metadata and metrics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, page: int, page_size: int) -> tuple[tuple[EvaluationJob, ...], int]:
        total = (await self.session.execute(select(func.count()).select_from(EvaluationJob))).scalar_one()
        rows = tuple(
            (
                await self.session.execute(
                    select(EvaluationJob)
                    .order_by(EvaluationJob.created_at.desc(), EvaluationJob.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    async def get(self, evaluation_id: UUID, *, lock: bool = False) -> EvaluationJob | None:
        statement = select(EvaluationJob).where(EvaluationJob.id == evaluation_id)
        if lock:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_many(self, evaluation_ids: tuple[UUID, ...]) -> tuple[EvaluationJob, ...]:
        return tuple(
            (
                await self.session.execute(
                    select(EvaluationJob)
                    .where(EvaluationJob.id.in_(evaluation_ids))
                    .order_by(EvaluationJob.created_at, EvaluationJob.id)
                )
            )
            .scalars()
            .all()
        )

    async def metrics_for(self, evaluation_ids: tuple[UUID, ...]) -> dict[UUID, tuple[EvaluationMetric, ...]]:
        if not evaluation_ids:
            return {}
        rows = tuple(
            (
                await self.session.execute(
                    select(EvaluationMetric)
                    .where(EvaluationMetric.evaluation_id.in_(evaluation_ids))
                    .order_by(
                        EvaluationMetric.evaluation_id,
                        EvaluationMetric.name,
                        EvaluationMetric.slice_name,
                    )
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[UUID, list[EvaluationMetric]] = {}
        for metric in rows:
            grouped.setdefault(metric.evaluation_id, []).append(metric)
        return {key: tuple(value) for key, value in grouped.items()}

    async def ready_dataset_version(self, dataset_id: UUID, version: int) -> DatasetVersion | None:
        statement = (
            select(DatasetVersion)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(
                Dataset.id == dataset_id,
                Dataset.deleted_at.is_(None),
                DatasetVersion.version == version,
                DatasetVersion.frozen_at.is_not(None),
                DatasetVersion.validation_status == "valid",
                DatasetVersion.contains_sensitive_data.is_(False),
            )
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def target_exists(self, target_type: str, target_id: UUID | None) -> bool:
        if target_type in {"rag", "system"}:
            return True
        model = {
            "agent": AgentDefinition,
            "tool": ToolDefinition,
            "model": ModelVersion,
        }[target_type]
        return (await self.session.execute(select(model.id).where(model.id == target_id))).scalar_one_or_none() is not None

    async def claim(self, limit: int = 1) -> tuple[EvaluationJob, ...]:
        statement = (
            select(EvaluationJob)
            .where(EvaluationJob.status == "queued")
            .order_by(EvaluationJob.created_at, EvaluationJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple((await self.session.execute(statement)).scalars().all())

    def add(self, evaluation: EvaluationJob) -> None:
        self.session.add(evaluation)

    def add_metric(self, metric: EvaluationMetric) -> None:
        self.session.add(metric)


def evaluation_data(
    evaluation: EvaluationJob,
    metrics: tuple[EvaluationMetric, ...] = (),
) -> EvaluationData:
    return EvaluationData(
        id=evaluation.id,
        target_type=evaluation.target_type,
        target_id=evaluation.target_id,
        status=evaluation.status,
        config=redact(evaluation.config) or {},
        summary=redact(evaluation.summary) or {},
        metrics=tuple(
            EvaluationMetricData(
                name=metric.name,
                value=metric.value,
                unit=metric.unit,
                slice_name=metric.slice_name,
            )
            for metric in metrics
        ),
        report_key=evaluation.report_key,
        error_code=evaluation.error_code,
        created_at=evaluation.created_at,
        finished_at=evaluation.finished_at,
    )


class EvaluationService:
    def __init__(
        self,
        session: AsyncSession,
        repository: EvaluationRepository,
        idempotency: IdempotencyService,
        audit: AuditService,
        now=None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.idempotency = idempotency
        self.audit = audit
        self.now = now or (lambda: datetime.now(UTC))

    async def list(self, page: int, page_size: int) -> EvaluationPageData:
        evaluations, total = await self.repository.list(page, page_size)
        metric_map = await self.repository.metrics_for(tuple(item.id for item in evaluations))
        return EvaluationPageData(
            items=tuple(evaluation_data(item, metric_map.get(item.id, ())) for item in evaluations),
            pagination=PageMetaData(
                page=page,
                page_size=page_size,
                total=total,
                total_pages=ceil(total / page_size) if total else 0,
            ),
        )

    async def detail(self, evaluation_id: UUID) -> EvaluationData:
        evaluation = await self.repository.get(evaluation_id)
        if evaluation is None:
            raise EvaluationNotFound()
        metric_map = await self.repository.metrics_for((evaluation_id,))
        return evaluation_data(evaluation, metric_map.get(evaluation_id, ()))

    async def create(
        self,
        actor: AuthenticatedUser,
        command: EvaluationCreateCommand,
        idempotency_key: str,
        request_id: str,
    ) -> tuple[int, dict[str, Any], str]:
        body = command.model_dump(mode="json")
        async with self.session.begin():
            decision = await self.idempotency.begin(
                user_id=actor.user_id,
                endpoint="POST /api/v1/evaluations",
                idempotency_key=idempotency_key,
                request_body=body,
            )
            if decision.replay:
                return decision.replay.response_status, dict(decision.replay.response_body), str(decision.replay.response_body["request_id"])
            if decision.pending:
                raise IdempotencyConflict()
            if not await self.repository.target_exists(command.target_type, command.target_id):
                raise EvaluationTargetNotFound()
            dataset_version_id = None
            if command.dataset_id is not None and command.dataset_version is not None:
                dataset_version = await self.repository.ready_dataset_version(command.dataset_id, command.dataset_version)
                if dataset_version is None:
                    raise EvaluationDatasetNotReady()
                dataset_version_id = dataset_version.id
            now = self.now()
            evaluation = EvaluationJob(
                id=uuid4(),
                target_type=command.target_type,
                target_id=command.target_id,
                dataset_version_id=dataset_version_id,
                status="queued",
                config=redact(command.config) or {},
                summary={},
                report_key=None,
                error_code=None,
                created_by=actor.user_id,
                started_at=None,
                finished_at=None,
                created_at=now,
            )
            self.repository.add(evaluation)
            data = evaluation_data(evaluation)
            response = SuccessResponse(data=data, request_id=request_id, timestamp=now).model_dump(mode="json")
            self.audit.record_success(
                action="evaluation.create",
                resource_type="evaluation",
                resource_id=str(evaluation.id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                after_data={"target_type": command.target_type, "status": "queued"},
            )
            completed = await self.idempotency.complete(
                record_id=decision.record_id,
                response_status=202,
                response_body=response,
                resource_type="evaluation",
                resource_id=str(evaluation.id),
            )
            if not completed:
                raise IdempotencyConflict()
        return 202, response, request_id

    async def compare(self, evaluation_ids: tuple[UUID, ...]) -> EvaluationComparisonData:
        evaluations = await self.repository.get_many(evaluation_ids)
        by_id = {item.id: item for item in evaluations}
        if any(evaluation_id not in by_id for evaluation_id in evaluation_ids):
            raise EvaluationNotFound()
        if any(by_id[evaluation_id].status != "succeeded" for evaluation_id in evaluation_ids):
            raise EvaluationNotCompleted()
        metric_map = await self.repository.metrics_for(evaluation_ids)
        rows: list[EvaluationComparisonRow] = []
        names: set[str] = set()
        for evaluation_id in evaluation_ids:
            values: dict[str, float] = {}
            for metric in metric_map.get(evaluation_id, ()):
                key = metric.name if metric.slice_name == "all" else f"{metric.name}@{metric.slice_name}"
                values[key] = metric.value
                names.add(key)
            rows.append(EvaluationComparisonRow(evaluation_id=evaluation_id, metrics=dict(sorted(values.items()))))
        return EvaluationComparisonData(
            evaluation_ids=evaluation_ids,
            metric_names=tuple(sorted(names)),
            rows=tuple(rows),
        )
