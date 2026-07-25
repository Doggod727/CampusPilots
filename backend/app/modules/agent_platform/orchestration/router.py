import asyncio
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from app.modules.agent_platform.domain.contracts import RouteDecision
from app.modules.agent_platform.orchestration.errors import InvalidAgentInput


class LocalRouterPort(Protocol):
    async def route(self, text: str) -> RouteDecision | Mapping[str, Any]: ...


class DeepSeekRouterPort(Protocol):
    async def route(self, text: str) -> RouteDecision | Mapping[str, Any]: ...


_DOMAIN_ORDER = (
    "knowledge", "service", "community", "governance", "modelops"
)
_KEYWORDS = {
    "knowledge": (
        "知识", "文档", "规定", "政策", "图书馆", "问答", "knowledge",
    ),
    "service": (
        "指南", "办理", "报修", "工单", "电费", "充值", "宿舍", "材料",
        "service", "repair",
    ),
    "community": (
        "社区", "话题", "帖子", "树洞", "活动", "报名", "失物", "拾物", "丢失", "招领", "event", "lost",
    ),
    "governance": (
        "审核", "敏感词", "权限", "审计", "安全策略", "governance",
    ),
    "modelops": (
        "数据集", "训练", "模型", "评估", "微调", "lora", "modelops",
    ),
}


class RouterService:
    def __init__(
        self,
        *,
        confidence_threshold: float = 0.80,
        local_router: LocalRouterPort | None = None,
        deepseek_router: DeepSeekRouterPort | None = None,
        local_timeout_ms: int = 500,
        deepseek_timeout_ms: int = 60000,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if local_timeout_ms <= 0 or deepseek_timeout_ms <= 0:
            raise ValueError("router timeouts must be positive")
        self._threshold = Decimal(str(confidence_threshold))
        self._local_router = local_router
        self._deepseek_router = deepseek_router
        self._local_timeout = local_timeout_ms / 1000
        self._deepseek_timeout = deepseek_timeout_ms / 1000

    async def route(self, text: str) -> RouteDecision:
        normalized = text.strip()
        if not normalized or len(normalized) > 4000:
            raise InvalidAgentInput()

        # Natural-language routing is model-first. Keyword rules cannot understand
        # negation, context or the primary intent of a multi-domain sentence, so they
        # are retained only as a deterministic provider-outage fallback.
        remote = await self._route_with_port(
            self._deepseek_router, normalized, "deepseek", self._deepseek_timeout
        )
        if remote is not None:
            return remote.model_copy(update={"candidate_agents": ()}) if remote.confidence >= self._threshold else self._clarify()

        local = await self._route_with_port(
            self._local_router, normalized, "local_model", self._local_timeout
        )
        if local is not None:
            return local if local.confidence >= self._threshold else self._clarify()

        rule = self.route_by_rule(normalized)
        if rule.target_agent != "clarify" and rule.confidence >= self._threshold:
            return rule
        return self._clarify()

    @staticmethod
    def _clarify() -> RouteDecision:
        return RouteDecision(
            target_agent="clarify",
            confidence=Decimal("0"),
            source="rule",
            reason_code="ROUTE_CLARIFICATION_REQUIRED",
            candidate_agents=(),
        )

    @staticmethod
    def route_by_rule(text: str) -> RouteDecision:
        lowered = text.casefold()
        matches = [
            domain for domain in _DOMAIN_ORDER
            if any(keyword in lowered for keyword in _KEYWORDS[domain])
        ]
        if not matches:
            return RouteDecision(
                target_agent="clarify",
                confidence=Decimal("0.4000"),
                source="rule",
                reason_code="ROUTE_NO_RULE_MATCH",
                candidate_agents=(),
            )
        candidates = tuple(matches[:3]) if len(matches) > 1 else ()
        return RouteDecision(
            target_agent=matches[0],
            confidence=Decimal("0.9500" if len(matches) == 1 else "0.8500"),
            source="rule",
            reason_code="ROUTE_RULE_SINGLE" if len(matches) == 1 else "ROUTE_RULE_MULTI",
            candidate_agents=candidates,
        )

    async def _route_with_port(
        self,
        port: LocalRouterPort | DeepSeekRouterPort | None,
        text: str,
        expected_source: str,
        timeout: float,
    ) -> RouteDecision | None:
        if port is None:
            return None
        try:
            raw = await asyncio.wait_for(port.route(text), timeout=timeout)
            decision = RouteDecision.model_validate(raw)
            if decision.source != expected_source or decision.target_agent == "clarify":
                return None
            return decision
        except Exception:
            return None
