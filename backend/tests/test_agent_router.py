import asyncio
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import RouteDecision
from app.modules.agent_platform.orchestration.router import RouterService


class FakeRouter:
    def __init__(self, result=None, *, error: Exception | None = None, delay=0.0):
        self.result = result
        self.error = error
        self.delay = delay
        self.calls = 0

    async def route(self, text: str):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.parametrize(
    ("text", "target"),
    [
        ("图书馆规定是什么", "knowledge"),
        ("宿舍电费和报修", "service"),
        ("报名校园活动", "community"),
        ("检查敏感词和权限", "governance"),
        ("运行模型评估", "modelops"),
    ],
)
def test_rules_cover_all_domains_when_model_providers_are_unavailable(text, target) -> None:
    local = FakeRouter(error=RuntimeError("must not run"))
    deepseek = FakeRouter(error=RuntimeError("must not run"))
    service = RouterService(local_router=local, deepseek_router=deepseek)
    decision = asyncio.run(service.route(text))
    assert decision.target_agent == target
    assert decision.source == "rule"
    assert decision.confidence == Decimal("0.9500")
    assert local.calls == deepseek.calls == 1


def test_multi_domain_rule_is_stable_and_bounded_to_three_candidates() -> None:
    service = RouterService()
    decision = asyncio.run(service.route(
        "查询图书馆知识，宿舍报修，活动报名，权限审核和模型训练"
    ))
    assert decision.target_agent == "knowledge"
    assert decision.reason_code == "ROUTE_RULE_MULTI"
    assert decision.candidate_agents == ("community", "knowledge", "service")
    assert len(decision.candidate_agents) == 3


def test_deepseek_is_used_before_local_router() -> None:
    local = FakeRouter({
        "target_agent": "service", "confidence": "0.8500",
        "source": "local_model", "reason_code": "LOCAL_SERVICE",
    })
    deepseek = FakeRouter({
        "target_agent": "knowledge", "confidence": "0.9900",
        "source": "deepseek", "reason_code": "REMOTE_KNOWLEDGE",
    })
    decision = asyncio.run(RouterService(
        local_router=local, deepseek_router=deepseek
    ).route("请帮我处理一下"))
    assert decision.target_agent == "knowledge"
    assert decision.source == "deepseek"
    assert local.calls == 0
    assert deepseek.calls == 1


def test_deepseek_single_label_does_not_execute_candidate_agents() -> None:
    deepseek = FakeRouter({
        "target_agent": "service", "confidence": "0.9500", "source": "deepseek",
        "reason_code": "ELECTRICITY_QUERY", "candidate_agents": ["knowledge"],
    })
    decision = asyncio.run(RouterService(deepseek_router=deepseek).route("查询电费"))
    assert decision.target_agent == "service"
    assert decision.candidate_agents == ()


def test_low_confidence_deepseek_asks_for_clarification_without_rule_override() -> None:
    deepseek = FakeRouter({
        "target_agent": "knowledge", "confidence": "0.5000",
        "source": "deepseek", "reason_code": "REMOTE_UNCERTAIN",
    })
    decision = asyncio.run(RouterService(deepseek_router=deepseek).route("查询电费"))
    assert decision.target_agent == "clarify"
    assert decision.reason_code == "ROUTE_CLARIFICATION_REQUIRED"


def test_invalid_local_and_dependency_failure_safely_fall_back() -> None:
    local = FakeRouter({
        "target_agent": "untrusted", "confidence": 1,
        "source": "local_model", "reason_code": "INVALID",
    })
    deepseek = FakeRouter({
        "target_agent": "community", "confidence": "0.9000",
        "source": "deepseek", "reason_code": "REMOTE_COMMUNITY",
    })
    decision = asyncio.run(RouterService(
        local_router=local, deepseek_router=deepseek
    ).route("请帮我处理一下"))
    assert decision.target_agent == "community"
    assert local.calls == 0
    assert deepseek.calls == 1

    failed = RouterService(
        local_router=FakeRouter(error=RuntimeError("private local failure")),
        deepseek_router=FakeRouter(error=RuntimeError("private remote failure")),
    )
    clarify = asyncio.run(failed.route("请帮我处理一下"))
    assert clarify.target_agent == "clarify"
    assert clarify.reason_code == "ROUTE_CLARIFICATION_REQUIRED"
    assert "private" not in repr(clarify)


def test_router_timeouts_fall_back_without_leaking_provider_details() -> None:
    local = FakeRouter(result=None, delay=0.05)
    deepseek = FakeRouter(result=None, delay=0.05)
    decision = asyncio.run(RouterService(
        local_router=local, deepseek_router=deepseek,
        local_timeout_ms=5, deepseek_timeout_ms=5,
    ).route("ambiguous request"))
    assert decision.target_agent == "clarify"
    assert local.calls == deepseek.calls == 1


def test_deepseek_provider_faults_safely_fall_back_to_clarify() -> None:
    from app.modules.agent_platform.deepseek import DeepSeekTimeout, DeepSeekUnavailable
    for fault in (DeepSeekUnavailable(), DeepSeekTimeout()):
        decision = asyncio.run(RouterService(
            deepseek_router=FakeRouter(error=fault),
        ).route("请帮我处理一下"))
        assert decision.target_agent == "clarify"
        assert decision.reason_code == "ROUTE_CLARIFICATION_REQUIRED"


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001])
def test_router_rejects_invalid_input_safely(text: str) -> None:
    with pytest.raises(AppError) as error:
        asyncio.run(RouterService().route(text))
    assert (error.value.status_code, error.value.code) == (422, "AGENT_INPUT_INVALID")
    if text:
        assert text not in str(error.value)
