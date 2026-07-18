import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_platform import evaluation_providers as providers
from app.modules.agent_platform.evaluation_worker import EvaluatorRegistry

CREATOR = uuid4()


def evaluation(**overrides):
    return SimpleNamespace(
        id=uuid4(), target_type=overrides.get("target_type", "rag"),
        target_id=overrides.get("target_id", uuid4()),
        config=overrides.get("config", {}), created_by=CREATOR,
    )


def citation(content: str):
    return SimpleNamespace(content=content)


def write_cases(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases), encoding="utf-8")
    return path


def test_load_frozen_cases_reads_project_fixture():
    cases = providers.load_frozen_cases()
    assert len(cases) == 30
    assert all("question" in case and "expected_keywords" in case for case in cases)


def test_rag_provider_computes_real_keyword_metrics(tmp_path):
    cases_path = write_cases(tmp_path, [
        {"id": 1, "question": "q1", "expected_keywords": ["校区"], "fallback": False},
        {"id": 2, "question": "q2", "expected_keywords": ["图书馆"], "fallback": False},
        {"id": 3, "question": "q3", "expected_keywords": ["不存在"], "fallback": True},
    ])

    class Retrieval:
        async def authorized_knowledge_bases(self, user):
            return [uuid4()]

        async def search(self, user, question, scope, top_k):
            if question == "q1":
                return SimpleNamespace(citations=(citation("其他"), citation("望江校区地址"),), confidence=0.5)
            if question == "q2":
                return SimpleNamespace(citations=(citation("食堂菜单"),), confidence=0.4)
            return SimpleNamespace(citations=(), confidence=0.0)

    @asynccontextmanager
    async def factory():
        yield Retrieval()

    provider = providers.RagEvaluationProvider(factory, AsyncMock(return_value=SimpleNamespace(user_id=CREATOR, permissions=(), department=None)), cases_path)
    outcome = asyncio.run(provider.evaluate(evaluation()))
    metrics = {item.name: item.value for item in outcome.metrics}
    # q1 命中 rank2(recip 0.5)，q2 未命中，q3 兜底正确(hit) → hits=2/3，mrr=0.5/3，coverage=2/3
    assert metrics["recall_at_k"] == round(2 / 3, 4)
    assert metrics["mrr"] == round(0.5 / 3, 4)
    assert metrics["citation_coverage"] == round(2 / 3, 4)
    assert outcome.summary == {"cases": 3}


def test_agent_provider_counts_completion_and_schema_validity():
    class Specialist:
        async def invoke(self, task, user):
            return SimpleNamespace(
                result=SimpleNamespace(status="succeeded", structured_output={"answer": "四川大学位于成都。"}),
                tool_request=None,
            )

    provider = providers.AgentEvaluationProvider(lambda: Specialist(), tasks=("t1", "t2"))
    outcome = asyncio.run(provider.evaluate(evaluation(target_type="agent")))
    metrics = {item.name: item.value for item in outcome.metrics}
    assert metrics == {"completion_rate": 1.0, "schema_valid_rate": 1.0, "cases": 2.0}


def test_tool_provider_executes_r0_and_rejects_write_tools():
    class Handler:
        async def __call__(self, invocation, payload):
            from app.modules.agent_platform.tool_gateway.catalog import KnowledgeSearchOutput

            return KnowledgeSearchOutput(items=(), retrieval_version="m1-rag-v1", fallback_reason="NO_RELIABLE_CONTEXT")

    env = AsyncMock(return_value=({"knowledge.search": Handler()}, {"knowledge.search": "r0"}, SimpleNamespace(user_id=CREATOR, permissions=(), department=None)))
    provider = providers.ToolEvaluationProvider(env, cases={"knowledge.search": {"query": "校区"}})
    outcome = asyncio.run(provider.evaluate(evaluation(target_type="tool")))
    metrics = {item.name: item.value for item in outcome.metrics}
    assert metrics == {"success_rate": 1.0, "schema_valid_rate": 1.0, "cases": 1.0}

    env2 = AsyncMock(return_value=({"work_order.create": Handler()}, {"work_order.create": "r2"}, SimpleNamespace()))
    provider2 = providers.ToolEvaluationProvider(env2, cases={"work_order.create": {"x": 1}})
    with pytest.raises(LookupError):
        asyncio.run(provider2.evaluate(evaluation(target_type="tool")))


def test_model_provider_requires_deepseek_target_and_measures_latency():
    class Gateway:
        async def json_completion(self, _messages):
            return {"answer": "2"}

    lookup = AsyncMock(return_value=SimpleNamespace(provider="deepseek"))
    provider = providers.ModelEvaluationProvider(lookup, Gateway, prompts=("p1", "p2"))
    outcome = asyncio.run(provider.evaluate(evaluation(target_type="model")))
    metrics = {item.name: item.value for item in outcome.metrics}
    assert metrics["success_rate"] == 1.0 and metrics["cases"] == 2.0
    assert metrics["latency_avg"] >= 0

    local = providers.ModelEvaluationProvider(AsyncMock(return_value=SimpleNamespace(provider="local")), Gateway)
    with pytest.raises(LookupError):
        asyncio.run(local.evaluate(evaluation(target_type="model")))


def test_system_provider_composes_real_checks():
    class Retrieval:
        async def authorized_knowledge_bases(self, user):
            return [uuid4()]

        async def search(self, user, question, scope, top_k):
            return SimpleNamespace(citations=(citation("校区"),))

    @asynccontextmanager
    async def factory():
        yield Retrieval()

    provider = providers.SystemEvaluationProvider(
        factory,
        AsyncMock(return_value=SimpleNamespace(user_id=CREATOR, permissions=(), department=None)),
        AsyncMock(),
        AsyncMock(),
    )
    outcome = asyncio.run(provider.evaluate(evaluation(target_type="system")))
    metrics = {item.name: item.value for item in outcome.metrics}
    assert metrics == {"end_to_end_success": 1.0, "checks_passed": 3.0}


def test_local_registry_wires_all_five_real_providers():
    settings = SimpleNamespace(
        modelops_execution_mode="local",
        deepseek_api_key=SimpleNamespace(get_secret_value=lambda: "key"),
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        knowledge_embedding_model_path="/models/bge",
        knowledge_chroma_path="/data/chroma",
        knowledge_score_threshold=0.35,
    )
    registry = EvaluatorRegistry(providers.build_local_evaluators(settings, lambda: None))
    for target_type in ("agent", "tool", "model", "rag", "system"):
        assert registry.resolve(target_type) is not None
