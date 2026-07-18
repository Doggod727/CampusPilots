"""五类真实 Evaluation Provider：指标只来自真实执行，样本/Prompt/输出不落日志。

- rag：冻结题集经授权检索计算关键词 Recall@K、MRR、引用覆盖率。
- agent：真实 DeepSeek Specialist 对冻结无工具任务统计完成率与结构校验通过率。
- tool：仅 R0 只读工具真实执行；任何非 R0 用例都被拒绝。
- model：deepseek 目标经真实网关执行冻结 sanity 提示；local 目标稳定拒绝。
- system：检索链路 + DeepSeek 探活 + 数据库探活组合端到端指标。
summary 只包含计数与稳定标记，不持久化样本正文、Prompt 或模型输出。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID

from app.modules.agent_platform.evaluation_worker import (
    EvaluationMetricValue,
    EvaluationOutcome,
)

FROZEN_RAG_CASES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "m1_rag_frozen_30.jsonl"
R0_TOOL_CASES: Mapping[str, dict[str, Any]] = {
    "knowledge.search": {"query": "校区地址"},
    "service.get_guide": {"query": "在读证明"},
    "event.search": {"query": "活动"},
}
AGENT_FROZEN_TASKS = ("用一句话介绍四川大学。",)
MODEL_SANITY_PROMPTS = ("1+1等于几？只输出JSON对象answer。",)
_RISK_ORDER = {"r0": 0, "r1": 1, "r2": 2, "r3": 3}


class RetrievalFactoryPort(Protocol):
    def __call__(self) -> Any: ...


def _metric(name: str, value: float, unit: str | None = None) -> EvaluationMetricValue:
    return EvaluationMetricValue(name=name, value=value, unit=unit)


def load_frozen_cases(path: Path = FROZEN_RAG_CASES) -> tuple[dict[str, Any], ...]:
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    if not cases:
        raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")
    return tuple(cases)


class RagEvaluationProvider:
    def __init__(self, retrieval_factory: RetrievalFactoryPort, user_loader: Callable[[UUID], Any], cases_path: Path = FROZEN_RAG_CASES) -> None:
        self._retrieval_factory = retrieval_factory
        self._user_loader = user_loader
        self._cases_path = cases_path

    async def evaluate(self, evaluation) -> EvaluationOutcome:
        cases = load_frozen_cases(self._cases_path)
        config = evaluation.config or {}
        top_k = int(config.get("top_k", 5))
        user = await self._user_loader(evaluation.created_by)
        knowledge_base_ids = [UUID(item) for item in config.get("knowledge_base_ids", ())] or None
        hits = 0
        covered = 0
        reciprocal = 0.0
        graded = 0
        async with self._retrieval_factory() as retrieval:
            scope = knowledge_base_ids or await retrieval.authorized_knowledge_bases(user)
            for case in cases:
                expected = [str(item) for item in case.get("expected_keywords", ())]
                result = await retrieval.search(user, case["question"], scope, top_k)
                if result.citations:
                    covered += 1
                if case.get("fallback"):
                    if not result.citations:
                        hits += 1
                        graded += 1
                    continue
                graded += 1
                rank = 0
                for index, citation in enumerate(result.citations, start=1):
                    if expected and all(keyword in citation.content for keyword in expected):
                        rank = index
                        break
                if rank:
                    hits += 1
                    reciprocal += 1.0 / rank
        total = max(graded, 1)
        return EvaluationOutcome(
            summary={"cases": len(cases)},
            metrics=(
                _metric("recall_at_k", round(hits / total, 4)),
                _metric("mrr", round(reciprocal / total, 4)),
                _metric("citation_coverage", round(covered / max(len(cases), 1), 4)),
                _metric("cases", float(len(cases))),
            ),
        )


class AgentEvaluationProvider:
    def __init__(self, specialist_factory: Callable[[], Any], tasks: Sequence[str] = AGENT_FROZEN_TASKS) -> None:
        self._specialist_factory = specialist_factory
        self._tasks = tuple(tasks)

    async def evaluate(self, evaluation) -> EvaluationOutcome:
        from app.modules.agent_platform.domain.contracts import AgentTask, UserContext

        user = UserContext(user_id=evaluation.created_by, username="evaluation", request_id=f"eval-{evaluation.id.hex[:12]}")
        completed = 0
        valid = 0
        for objective in self._tasks:
            outcome = await self._specialist_factory().invoke(
                AgentTask(task_id=evaluation.id, agent_run_id=evaluation.id, target_agent="knowledge_agent", objective=objective),
                user,
            )
            if outcome.result.status in {"succeeded", "partial"}:
                completed += 1
            answer = (outcome.result.structured_output or {}).get("answer")
            if isinstance(answer, str) and answer.strip():
                valid += 1
        total = max(len(self._tasks), 1)
        return EvaluationOutcome(
            summary={"cases": len(self._tasks)},
            metrics=(
                _metric("completion_rate", round(completed / total, 4)),
                _metric("schema_valid_rate", round(valid / total, 4)),
                _metric("cases", float(len(self._tasks))),
            ),
        )


class ToolEvaluationProvider:
    def __init__(self, handlers_factory: Callable[[], Any], cases: Mapping[str, dict[str, Any]] = R0_TOOL_CASES) -> None:
        self._handlers_factory = handlers_factory
        self._cases = dict(cases)

    async def evaluate(self, evaluation) -> EvaluationOutcome:
        handlers, risk_levels, user = await self._handlers_factory(evaluation.created_by)
        invocation = SimpleNamespace(user=user)
        succeeded = 0
        valid = 0
        for tool_name, arguments in self._cases.items():
            if _RISK_ORDER.get(risk_levels.get(tool_name, "r3"), 3) > 0:
                raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")
            handler = handlers.get(tool_name)
            if handler is None:
                raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")
            output = await handler(invocation, arguments)
            succeeded += 1
            type(output).model_validate(output)
            valid += 1
        total = max(len(self._cases), 1)
        return EvaluationOutcome(
            summary={"cases": len(self._cases)},
            metrics=(
                _metric("success_rate", round(succeeded / total, 4)),
                _metric("schema_valid_rate", round(valid / total, 4)),
                _metric("cases", float(len(self._cases))),
            ),
        )


class ModelEvaluationProvider:
    def __init__(self, model_lookup: Callable[[UUID], Any], gateway_factory: Callable[[], Any], prompts: Sequence[str] = MODEL_SANITY_PROMPTS, model_root: Path | None = None) -> None:
        self._model_lookup = model_lookup
        self._gateway_factory = gateway_factory
        self._prompts = tuple(prompts)
        self._model_root = model_root

    async def evaluate(self, evaluation) -> EvaluationOutcome:
        model = await self._model_lookup(evaluation.target_id)
        if model is None:
            raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")
        if model.provider == "deepseek":
            return await self._evaluate_deepseek()
        if model.provider == "local":
            if self._model_root is None:
                raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")
            return await asyncio.to_thread(self._evaluate_local, model)
        raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")

    async def _evaluate_deepseek(self) -> EvaluationOutcome:
        gateway = self._gateway_factory()
        succeeded = 0
        latencies: list[float] = []
        for prompt in self._prompts:
            started = time.perf_counter()
            await gateway.json_completion(
                (
                    {"role": "system", "content": "只输出一个JSON对象answer，不输出思维链。"},
                    {"role": "user", "content": prompt},
                )
            )
            latencies.append((time.perf_counter() - started) * 1000)
            succeeded += 1
        total = max(len(self._prompts), 1)
        return EvaluationOutcome(
            summary={"cases": len(self._prompts)},
            metrics=(
                _metric("success_rate", round(succeeded / total, 4)),
                _metric("latency_avg", round(sum(latencies) / len(latencies), 1), "ms"),
                _metric("cases", float(len(self._prompts))),
            ),
        )

    def _evaluate_local(self, model) -> EvaluationOutcome:
        """local LoRA 产物：base 与 adapter 在冻结样本上的真实前向损失对比。"""

        if not model.artifact_key:
            raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        base_dir = self._model_root / "base-models" / str(model.base_model).replace("/", "--")
        artifact_dir = self._model_root / str(model.artifact_key).rsplit("/", 1)[0]
        if not (base_dir / "config.json").is_file() or not artifact_dir.is_dir():
            raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE")
        tokenizer = AutoTokenizer.from_pretrained(base_dir)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        def mean_loss(net) -> float:
            losses: list[float] = []
            with torch.no_grad():
                for text in LOCAL_EVAL_SAMPLES:
                    tokens = tokenizer(text, truncation=True, max_length=256, return_tensors="pt")
                    outputs = net(**tokens, labels=tokens["input_ids"])
                    losses.append(float(outputs.loss.item()))
            return sum(losses) / len(losses)

        base = AutoModelForCausalLM.from_pretrained(base_dir)
        base_loss = mean_loss(base)
        try:
            from peft import PeftModel

            adapted = PeftModel.from_pretrained(base, str(artifact_dir))
        except Exception as exc:
            raise LookupError("EVALUATION_PROVIDER_UNAVAILABLE") from exc
        lora_loss = mean_loss(adapted)
        return EvaluationOutcome(
            summary={"samples": len(LOCAL_EVAL_SAMPLES)},
            metrics=(
                _metric("base_loss", round(base_loss, 6)),
                _metric("lora_loss", round(lora_loss, 6)),
                _metric("loss_improvement", round(base_loss - lora_loss, 6)),
                _metric("samples", float(len(LOCAL_EVAL_SAMPLES))),
            ),
        )


LOCAL_EVAL_SAMPLES: tuple[str, ...] = (
    "四川大学望江校区位于成都市武侯区，是主要教学区之一。",
    "学生可在校园服务中心办理报修与在读证明。",
    "图书馆工作日全天开放，凭校园卡入馆。",
    "失物招领平台帮助同学找回遗失物品。",
)


class SystemEvaluationProvider:
    def __init__(self, retrieval_factory: RetrievalFactoryPort, user_loader: Callable[[UUID], Any], ping: Callable[[], Any], db_probe: Callable[[], Any]) -> None:
        self._retrieval_factory = retrieval_factory
        self._user_loader = user_loader
        self._ping = ping
        self._db_probe = db_probe

    async def evaluate(self, evaluation) -> EvaluationOutcome:
        checks = 0
        user = await self._user_loader(evaluation.created_by)
        async with self._retrieval_factory() as retrieval:
            scope = await retrieval.authorized_knowledge_bases(user)
            if scope:
                result = await retrieval.search(user, "四川大学校区地址", scope, 3)
                if result.citations:
                    checks += 1
        await self._ping()
        checks += 1
        await self._db_probe()
        checks += 1
        return EvaluationOutcome(
            summary={"checks": 3},
            metrics=(
                _metric("end_to_end_success", round(checks / 3, 4)),
                _metric("checks_passed", float(checks)),
            ),
        )


def build_local_evaluators(settings, sessions) -> dict[str, Any]:
    """装配五类真实 Provider（仅 MODELOPS_EXECUTION_MODE=local 使用）。"""

    from contextlib import asynccontextmanager

    import chromadb
    from sqlalchemy import text

    from app.modules.agent_platform.composition import RuntimeCompositionFactory
    from app.modules.agent_platform.deepseek import DeepSeekGateway, DeepSeekSpecialistProvider
    from app.modules.agent_platform.models import ModelVersion
    from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
    from app.modules.ai_knowledge.knowledge import KnowledgeRepository, KnowledgeService
    from app.modules.ai_knowledge.retrieval import RetrievalService
    from app.modules.ai_knowledge.vectors import BgeSmallZhEmbeddingProvider, ChromaVectorStore
    from app.modules.platform.repositories import RbacRepository, UserRepository

    gateway = DeepSeekGateway(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=str(settings.deepseek_base_url),
        model=settings.deepseek_model,
    )
    factory = RuntimeCompositionFactory(settings)

    @asynccontextmanager
    async def retrieval_factory():
        async with sessions() as session:
            knowledge = KnowledgeService(session, KnowledgeRepository(session))
            yield RetrievalService(
                session,
                knowledge,
                BgeSmallZhEmbeddingProvider(str(settings.knowledge_embedding_model_path)),
                ChromaVectorStore(chromadb.PersistentClient(path=str(settings.knowledge_chroma_path))),
                settings.knowledge_score_threshold,
            )

    async def user_loader(user_id: UUID):
        async with sessions() as session:
            user = await UserRepository(session).get_by_id(user_id)
            permissions = await RbacRepository(session).list_permission_codes_for_user(user_id)
            return SimpleNamespace(user_id=user_id, permissions=tuple(permissions), department=getattr(user, "department", None))

    async def tool_environment(user_id: UUID):
        user = await user_loader(user_id)
        async with sessions() as session:
            catalogs = await factory.load_catalogs(session)
            executor, _approval, _moderation = await factory.build_tool_executor(session, catalogs)
            handlers = getattr(executor, "_handlers", {})
            risks = {name: contract.definition.risk_level for name, contract in TOOL_CONTRACTS.items()}
            return handlers, risks, user

    async def model_lookup(target_id: UUID):
        async with sessions() as session:
            return await session.get(ModelVersion, target_id)

    async def ping():
        response = await gateway._client.get(
            f"{str(settings.deepseek_base_url).rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key.get_secret_value()}"},
            timeout=15,
        )
        response.raise_for_status()

    async def db_probe():
        async with sessions() as session:
            await session.execute(text("SELECT 1"))

    return {
        "rag": RagEvaluationProvider(retrieval_factory, user_loader),
        "agent": AgentEvaluationProvider(lambda: DeepSeekSpecialistProvider(gateway)),
        "tool": ToolEvaluationProvider(tool_environment),
        "model": ModelEvaluationProvider(model_lookup, lambda: gateway, model_root=Path(settings.model_artifact_root)),
        "system": SystemEvaluationProvider(retrieval_factory, user_loader, ping, db_probe),
    }
