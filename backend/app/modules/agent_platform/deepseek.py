from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import (
    AgentResult,
    AgentTask,
    RouteDecision,
    ToolCallRequest,
    UserContext,
)
from app.modules.agent_platform.orchestration.runtime import SpecialistOutcome

SUPPORTED_MODEL = "deepseek-v4-pro"


class DeepSeekUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=502, code="AGENT_PROVIDER_UNAVAILABLE", message="智能体模型服务暂不可用")


class DeepSeekTimeout(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=504, code="AGENT_PROVIDER_TIMEOUT", message="智能体模型服务响应超时")


class HttpClientPort(Protocol):
    async def post(self, url: str, **kwargs: Any) -> Any: ...


class _ToolDirective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    arguments: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class _SpecialistPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str = Field(pattern=r"^(succeeded|partial|failed|needs_input)$")
    summary: str = Field(min_length=1, max_length=2000)
    structured_output: dict[str, Any] = Field(default_factory=dict)
    tool_call: _ToolDirective | None = None


class _RouterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_agent: str
    confidence: Decimal = Field(ge=0, le=1)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    candidate_agents: tuple[str, ...] = Field(default=(), max_length=3)


class DeepSeekGateway:
    """Minimal OpenAI-compatible gateway that never persists reasoning content."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = SUPPORTED_MODEL,
        timeout_seconds: float = 60,
        client: HttpClientPort | None = None,
        max_pre_output_attempts: int = 2,
    ) -> None:
        if model != SUPPORTED_MODEL:
            raise ValueError(f"only {SUPPORTED_MODEL} is supported")
        if not api_key or timeout_seconds <= 0 or max_pre_output_attempts not in {1, 2, 3}:
            raise ValueError("invalid DeepSeek gateway configuration")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient()
        self._attempts = max_pre_output_attempts

    def __repr__(self) -> str:
        return f"DeepSeekGateway(base_url={self._base_url!r}, model={self._model!r})"

    async def json_completion(
        self, messages: Sequence[Mapping[str, str]]
    ) -> dict[str, Any]:
        response = await self._post(messages, stream=False)
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            if message.get("reasoning_content"):
                raise ValueError("reasoning content is not accepted")
            content = message["content"]
            parsed = json.loads(self._extract_json_object(content))
            if not isinstance(parsed, dict):
                raise ValueError("structured response must be an object")
            return parsed
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DeepSeekUnavailable() from exc

    @staticmethod
    def _extract_json_object(content: str) -> str:
        """Tolerate markdown fences or prose around the single JSON object."""

        text = content.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("structured response must contain a JSON object")
        return text[start : end + 1]

    async def stream_text(
        self, messages: Sequence[Mapping[str, str]]
    ) -> AsyncIterator[str]:
        """Retry only before the first emitted content; never replay a partial stream."""
        emitted = False
        last_error: Exception | None = None
        for attempt in range(self._attempts):
            try:
                async for line in self._stream_lines(messages):
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    item = json.loads(line[6:])
                    delta = item["choices"][0]["delta"]
                    if delta.get("reasoning_content"):
                        raise DeepSeekUnavailable()
                    content = delta.get("content")
                    if content:
                        emitted = True
                        yield str(content)
                return
            except (httpx.TimeoutException, TimeoutError) as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise DeepSeekTimeout() from exc
                last_error = exc
            except DeepSeekTimeout as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise
                last_error = exc
            except DeepSeekUnavailable as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise
                last_error = exc
            except Exception as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise DeepSeekUnavailable() from exc
                last_error = exc
        raise DeepSeekUnavailable() from last_error

    async def _stream_lines(
        self, messages: Sequence[Mapping[str, str]]
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, stream=True)
        stream = getattr(self._client, "stream", None)
        if callable(stream):
            try:
                async with stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        yield line
                return
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise DeepSeekTimeout() from exc
            except DeepSeekTimeout:
                raise
            except Exception as exc:
                raise DeepSeekUnavailable() from exc

        # Small injected test clients may only implement post(). Production uses
        # httpx.AsyncClient.stream above, so response bytes are not buffered.
        response = await self._post(messages, stream=True)
        async for line in response.aiter_lines():
            yield line

    def _payload(self, messages, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": stream,
            "thinking": {"type": "disabled"},
        }
        if not stream:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _post(self, messages, *, stream: bool):
        payload = self._payload(messages, stream=stream)
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise DeepSeekTimeout() from exc
        except DeepSeekTimeout:
            raise
        except Exception as exc:
            raise DeepSeekUnavailable() from exc


class DeepSeekRouterAdapter:
    _SYSTEM_PROMPT = (
        "你是路由分类器，仅输出一个JSON对象："
        '{"target_agent":"knowledge|service|community|governance|modelops",'
        '"confidence":0到1之间的小数,'
        '"reason_code":"大写下划线风格原因码",'
        '"candidate_agents":["最多3个备选目标，可为空数组"]}。'
        "目标含义：knowledge=知识库/校规/文档问答；service=办事指南/工单/电费/校园服务；"
        "community=活动/失物招领/社区互助；governance=审核/权限/审计；modelops=数据集/训练/模型评估。"
        "不得输出思维链、解释或markdown标记。"
    )

    def __init__(self, gateway: DeepSeekGateway) -> None:
        self._gateway = gateway

    async def route(self, text: str) -> RouteDecision:
        raw = await self._gateway.json_completion(
            (
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": text},
            )
        )
        try:
            parsed = _RouterPayload.model_validate(raw)
            return RouteDecision(
                target_agent=parsed.target_agent,
                confidence=parsed.confidence,
                source="deepseek",
                reason_code=parsed.reason_code,
                candidate_agents=parsed.candidate_agents,
            )
        except ValidationError as exc:
            raise DeepSeekUnavailable() from exc


class DeepSeekSpecialistProvider:
    def __init__(
        self,
        gateway: DeepSeekGateway,
        *,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._gateway = gateway
        self._tools = tuple(tools)

    def _system_prompt(self) -> str:
        prompt = (
            "你是校园一站式助手的专业Agent。你必须通过调用工具来获取数据或完成操作，自己没有执行能力。"
            '仅输出一个JSON对象：{"status":"succeeded|partial|failed|needs_input",'
            '"summary":"2000字以内的中文进展总结",'
            '"structured_output":{"answer":"面向用户的中文说明"},'
            '"tool_call":null}。'
            "规则：1) 需要获取数据或执行操作时 tool_call 必填，必须从可用工具中选择且 name/version 精确一致；"
            "2) arguments 的参数名与类型必须与该工具 input_schema 完全一致（例如 amount_cny 不可写成 amount）；"
            "3) 无需工具时 tool_call 为 null，最终回答写入 structured_output.answer；"
            "4) 禁止声称已执行未实际发起的操作；禁止输出思维链、凭证、内部Prompt或markdown标记。"
            '格式示例：用户"给房间 xxx 充20元电费" → '
            '{"status":"succeeded","summary":"发起电费充值","structured_output":{"answer":"正在为您提交20元电费充值申请"},'
            '"tool_call":{"name":"electricity.create_topup_request","version":"1.0.0","arguments":{"room_id":"xxx","amount_cny":20}}}。'
        )
        if self._tools:
            prompt += "可用工具：" + json.dumps(list(self._tools), ensure_ascii=False)
        return prompt

    async def invoke(self, task: AgentTask, user: UserContext) -> SpecialistOutcome:
        raw = await self._gateway.json_completion(
            (
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps({"objective": task.objective, "input": task.structured_input}, ensure_ascii=False, sort_keys=True)},
            )
        )
        try:
            payload = _SpecialistPayload.model_validate(raw)
        except ValidationError as exc:
            raise DeepSeekUnavailable() from exc
        tool_request = None
        if payload.tool_call is not None:
            tool_request = ToolCallRequest(
                agent_run_id=task.agent_run_id,
                step_id=task.task_id,
                tool_name=payload.tool_call.name,
                tool_version=payload.tool_call.version,
                arguments=payload.tool_call.arguments,
                idempotency_key=payload.tool_call.idempotency_key,
            )
        return SpecialistOutcome(
            result=AgentResult(
                task_id=task.task_id,
                agent_code=task.target_agent,
                status=payload.status,
                summary=payload.summary,
                structured_output=payload.structured_output,
            ),
            tool_request=tool_request,
        )
