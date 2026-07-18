from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_platform.evaluations import EvaluationRepository
from app.modules.agent_platform.models import EvaluationJob, EvaluationMetric
from app.modules.platform.audit import redact


EVALUATION_PROVIDER_UNAVAILABLE = "EVALUATION_PROVIDER_UNAVAILABLE"
TARGET_TYPES = ("agent", "tool", "model", "rag", "system")


class EvaluationMetricValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100)
    value: float = Field(allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=30)
    slice_name: str = Field(default="all", min_length=1, max_length=100)


class EvaluationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: dict = Field(default_factory=dict)
    metrics: tuple[EvaluationMetricValue, ...]
    report_key: str | None = Field(default=None, max_length=500)


class EvaluatorPort(Protocol):
    async def evaluate(self, evaluation: EvaluationJob) -> EvaluationOutcome: ...


class EvaluatorRegistry:
    """Explicit evaluator lookup; missing providers fail closed."""

    def __init__(self, evaluators: Mapping[str, EvaluatorPort]) -> None:
        unknown = set(evaluators) - set(TARGET_TYPES)
        if unknown:
            raise ValueError("unsupported evaluation target type")
        self._evaluators = dict(evaluators)

    def resolve(self, target_type: str) -> EvaluatorPort:
        try:
            return self._evaluators[target_type]
        except KeyError as exc:
            raise LookupError(EVALUATION_PROVIDER_UNAVAILABLE) from exc


def build_production_evaluator_registry(settings) -> EvaluatorRegistry:
    """Production evaluator registry; Fake evaluators are reserved for test fixtures.

    MODELOPS_EXECUTION_MODE=local enables real provider wiring in the ModelOps
    execution batch; until then every target type fails closed with
    EVALUATION_PROVIDER_UNAVAILABLE and no fabricated metrics are produced.
    """

    if settings.modelops_execution_mode not in {"disabled", "local"}:
        raise ValueError("MODELOPS_EXECUTION_MODE must be disabled or local")
    return EvaluatorRegistry({})


class EvaluationWorker:
    """Claim and execute one evaluation using a fresh caller-provided Session."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        evaluators: EvaluatorRegistry,
        *,
        repository_factory: Callable[[AsyncSession], EvaluationRepository] = EvaluationRepository,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._evaluators = evaluators
        self._repository_factory = repository_factory
        self._now = now or (lambda: datetime.now(UTC))

    async def run_once(self) -> UUID | None:
        async with self._session_scope() as session:
            repository = self._repository_factory(session)
            async with session.begin():
                claimed = await repository.claim(1)
                if not claimed:
                    return None
                evaluation = claimed[0]
                evaluation.status = "running"
                evaluation.started_at = self._now()
                evaluation.error_code = None
                await session.flush()
                try:
                    evaluator = self._evaluators.resolve(evaluation.target_type)
                    outcome = await evaluator.evaluate(evaluation)
                except Exception:
                    evaluation.status = "failed"
                    evaluation.summary = {}
                    evaluation.report_key = None
                    evaluation.error_code = EVALUATION_PROVIDER_UNAVAILABLE
                    evaluation.finished_at = self._now()
                    return evaluation.id

                evaluation.status = "succeeded"
                evaluation.summary = redact(outcome.summary) or {}
                evaluation.report_key = outcome.report_key
                evaluation.finished_at = self._now()
                for value in outcome.metrics:
                    repository.add_metric(
                        EvaluationMetric(
                            id=uuid4(),
                            evaluation_id=evaluation.id,
                            name=value.name,
                            value=value.value,
                            unit=value.unit,
                            slice_name=value.slice_name,
                            created_at=evaluation.finished_at,
                        )
                    )
                return evaluation.id
