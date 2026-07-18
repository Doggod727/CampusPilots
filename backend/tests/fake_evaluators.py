"""Test-only deterministic evaluators; never wired into production entrypoints."""

from app.modules.agent_platform.evaluation_worker import (
    TARGET_TYPES,
    EvaluationMetricValue,
    EvaluationOutcome,
    EvaluatorRegistry,
)


class DeterministicFakeEvaluator:
    """Offline evaluator with fixed, non-production metrics."""

    METRICS = {
        "agent": ("completion_rate", 0.92),
        "tool": ("success_rate", 0.98),
        "model": ("accuracy", 0.90),
        "rag": ("recall_at_5", 0.88),
        "system": ("end_to_end_success", 0.90),
    }

    async def evaluate(self, evaluation) -> EvaluationOutcome:
        name, value = self.METRICS[evaluation.target_type]
        return EvaluationOutcome(
            summary={"mode": "deterministic_fake", "target_type": evaluation.target_type},
            metrics=(
                EvaluationMetricValue(name=name, value=value),
                EvaluationMetricValue(name="latency_p95", value=25.0, unit="ms"),
            ),
        )


def deterministic_fake_registry() -> EvaluatorRegistry:
    evaluator = DeterministicFakeEvaluator()
    return EvaluatorRegistry({target_type: evaluator for target_type in TARGET_TYPES})
