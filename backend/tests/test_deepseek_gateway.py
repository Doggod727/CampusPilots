import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.modules.agent_platform.deepseek import (
    DeepSeekGateway,
    DeepSeekRouterAdapter,
    DeepSeekSpecialistProvider,
    DeepSeekTimeout,
    DeepSeekUnavailable,
)
from app.modules.agent_platform.domain.contracts import AgentTask, UserContext


class FakeResponse:
    def __init__(self, body=None, lines=(), status=200):
        self.body = body or {}
        self.lines = tuple(lines)
        self.status = status

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("provider detail", request=None, response=None)

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeClient:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _StreamContext:
    def __init__(self, response): self.response = response
    async def __aenter__(self): return self.response
    async def __aexit__(self, *_args): return False


class TrueStreamClient:
    def __init__(self, response): self.response = response; self.requests = []
    def stream(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return _StreamContext(self.response)
    async def post(self, *_args, **_kwargs):
        raise AssertionError("streaming must not use buffered post")


def _completion(payload, *, reasoning=None):
    message = {"content": json.dumps(payload)}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return FakeResponse({"choices": [{"message": message}]})


def test_router_uses_supported_model_and_disables_thinking():
    client = FakeClient(_completion({
        "target_agent": "knowledge", "confidence": "0.91",
        "reason_code": "ROUTE_MODEL_KNOWLEDGE", "candidate_agents": [],
    }))
    gateway = DeepSeekGateway(api_key="secret-key", client=client)
    decision = asyncio.run(DeepSeekRouterAdapter(gateway).route("查询校规"))
    assert decision.target_agent == "knowledge"
    assert decision.source == "deepseek"
    sent = client.requests[0][1]
    assert sent["json"]["model"] == "deepseek-v4-pro"
    assert sent["json"]["thinking"] == {"type": "disabled"}
    assert "secret-key" not in repr(gateway)


def test_specialist_strictly_parses_result_and_tool_call():
    run_id, task_id = uuid4(), uuid4()
    client = FakeClient(_completion({
        "status": "succeeded", "summary": "已找到指南",
        "structured_output": {"answer": "安全摘要"},
        "tool_call": {"name": "service.get_guide", "version": "1.0.0", "arguments": {"query": "校历"}, "idempotency_key": None},
    }))
    provider = DeepSeekSpecialistProvider(DeepSeekGateway(api_key="secret", client=client))
    outcome = asyncio.run(provider.invoke(
        AgentTask(task_id=task_id, agent_run_id=run_id, target_agent="service_agent", objective="查询校历"),
        UserContext(user_id=uuid4(), username="student01", request_id="request-1234"),
    ))
    assert outcome.result.summary == "已找到指南"
    assert outcome.tool_request.tool_name == "service.get_guide"
    assert outcome.tool_request.agent_run_id == run_id


def test_specialist_prompt_requires_an_explicitly_selected_tool():
    run_id, task_id = uuid4(), uuid4()
    client = FakeClient(_completion({
        "status": "succeeded", "summary": "已查询",
        "structured_output": {"answer": "查询完成"},
        "tool_call": {"name": "service.get_guide", "version": "1.0.0", "arguments": {"query": "校历"}, "idempotency_key": None},
    }))
    provider = DeepSeekSpecialistProvider(DeepSeekGateway(api_key="secret", client=client))
    asyncio.run(provider.invoke(
        AgentTask(
            task_id=task_id, agent_run_id=run_id, target_agent="service_agent",
            objective="查询校历", structured_input={"requested_tool_names": ["service.get_guide"]},
        ),
        UserContext(user_id=uuid4(), username="student01", request_id="request-1234"),
    ))
    system_prompt = client.requests[0][1]["json"]["messages"][0]["content"]
    assert "tool_call 不得为 null" in system_prompt and "service.get_guide" in system_prompt
    assert "每次响应最多只能发起一个 tool_call" in system_prompt
    assert "不得编造多个对象ID" in system_prompt


def test_specialist_rejects_unsupported_event_publish_without_asking_for_input():
    run_id, task_id = uuid4(), uuid4()
    client = FakeClient()
    provider = DeepSeekSpecialistProvider(
        DeepSeekGateway(api_key="secret", client=client),
        tools=(
            {"name": "event.search", "version": "1.0.0"},
            {"name": "event.register", "version": "1.0.0"},
        ),
    )

    outcome = asyncio.run(provider.invoke(
        AgentTask(
            task_id=task_id,
            agent_run_id=run_id,
            target_agent="community_agent",
            objective="帮我发布校园活动",
        ),
        UserContext(user_id=uuid4(), username="student01", request_id="request-1234"),
    ))

    assert outcome.result.status == "partial"
    assert "暂不支持发布或创建校园活动" in outcome.result.structured_output["answer"]
    assert outcome.result.structured_output["unsupported_operation"] == "event.publish"
    assert outcome.tool_request is None
    assert client.requests == []


def test_specialist_uses_event_create_when_catalog_supports_it():
    run_id, task_id = uuid4(), uuid4()
    client = FakeClient(_completion({
        "status": "needs_input", "summary": "请补充活动信息",
        "structured_output": {"answer": "请提供活动标题、时间、地点和名额。", "missing_slots": ["title"]},
        "tool_call": None,
    }))
    provider = DeepSeekSpecialistProvider(
        DeepSeekGateway(api_key="secret", client=client),
        tools=({"name": "event.create", "version": "1.0.0", "input_schema": {}},),
    )
    outcome = asyncio.run(provider.invoke(
        AgentTask(task_id=task_id, agent_run_id=run_id,
                  target_agent="community_agent", objective="帮我创建校园活动"),
        UserContext(user_id=uuid4(), username="student01", request_id="request-1234"),
    ))
    assert outcome.result.status == "needs_input"
    assert len(client.requests) == 1


def test_specialist_never_invents_a_missing_electricity_topup_amount():
    run_id, task_id, room_id = uuid4(), uuid4(), uuid4()
    client = FakeClient()
    provider = DeepSeekSpecialistProvider(
        DeepSeekGateway(api_key="secret", client=client),
        tools=({"name": "electricity.create_topup_request", "version": "1.0.0"},),
    )

    outcome = asyncio.run(provider.invoke(
        AgentTask(
            task_id=task_id,
            agent_run_id=run_id,
            target_agent="service_agent",
            objective="帮我充电费",
        ),
        UserContext(
            user_id=uuid4(),
            username="student01",
            request_id="request-1234",
            room_ids=(room_id,),
        ),
    ))

    assert outcome.result.status == "needs_input"
    assert outcome.result.structured_output == {
        "answer": "请提供充值金额。",
        "missing_slots": ["amount_cny"],
    }
    assert outcome.tool_request is None
    assert client.requests == []


def test_reasoning_or_invalid_shape_is_safely_rejected():
    gateway = DeepSeekGateway(api_key="secret", client=FakeClient(_completion({"x": 1}, reasoning="hidden chain")))
    with pytest.raises(DeepSeekUnavailable) as caught:
        asyncio.run(gateway.json_completion(({"role": "user", "content": "test"},)))
    assert "hidden chain" not in str(caught.value)


def test_timeout_is_mapped_without_provider_details():
    gateway = DeepSeekGateway(api_key="secret", client=FakeClient(httpx.ReadTimeout("private provider error")))
    with pytest.raises(DeepSeekTimeout) as caught:
        asyncio.run(gateway.json_completion(({"role": "user", "content": "test"},)))
    assert caught.value.status_code == 504
    assert "private provider error" not in str(caught.value)


def test_stream_retries_only_before_first_content():
    timeout = httpx.ReadTimeout("first attempt")
    success = FakeResponse(lines=(
        'data: {"choices":[{"delta":{"content":"A"}}]}',
        'data: [DONE]',
    ))
    client = FakeClient(timeout, success)
    gateway = DeepSeekGateway(api_key="secret", client=client, max_pre_output_attempts=2)
    async def collect():
        return [chunk async for chunk in gateway.stream_text(({"role": "user", "content": "x"},))]
    chunks = asyncio.run(collect())
    assert chunks == ["A"]
    assert len(client.requests) == 2


def test_json_completion_retries_transient_unavailable_before_parsing():
    client = FakeClient(
        httpx.ConnectError("cold connection"),
        _completion({"answer": "available after retry"}),
    )
    gateway = DeepSeekGateway(
        api_key="secret", client=client, max_pre_output_attempts=2
    )

    result = asyncio.run(
        gateway.json_completion(({"role": "user", "content": "test"},))
    )

    assert result == {"answer": "available after retry"}
    assert len(client.requests) == 2


def test_stream_uses_http_stream_transport_without_buffered_post():
    client = TrueStreamClient(FakeResponse(lines=(
        'data: {"choices":[{"delta":{"content":"实时"}}]}',
        'data: [DONE]',
    )))
    gateway = DeepSeekGateway(api_key="secret", client=client)

    async def collect():
        return [chunk async for chunk in gateway.stream_text(({"role": "user", "content": "x"},))]

    assert asyncio.run(collect()) == ["实时"]
    assert client.requests[0][0] == "POST"


def test_gateway_rejects_unapproved_model():
    with pytest.raises(ValueError):
        DeepSeekGateway(api_key="secret", model="deepseek-chat")


def test_connect_error_maps_to_502_without_transport_details():
    gateway = DeepSeekGateway(api_key="secret", client=FakeClient(httpx.ConnectError("dial 10.0.0.9:443 refused")))
    with pytest.raises(DeepSeekUnavailable) as caught:
        asyncio.run(gateway.json_completion(({"role": "user", "content": "test"},)))
    assert caught.value.status_code == 502
    assert "dial" not in str(caught.value) and caught.value.details == []


def test_http_error_status_maps_to_502_without_upstream_body():
    class StatusResponse(FakeResponse):
        def raise_for_status(self):
            request = httpx.Request("POST", "https://provider.example/chat/completions")
            httpx.Response(self.status, request=request, json=self.body).raise_for_status()

    leaky = StatusResponse(body={"error": {"message": "invalid api key sk-probe-leak"}}, status=401)
    gateway = DeepSeekGateway(api_key="sk-probe-leak", client=FakeClient(leaky))
    with pytest.raises(DeepSeekUnavailable) as caught:
        asyncio.run(gateway.json_completion(({"role": "user", "content": "test"},)))
    assert caught.value.status_code == 502
    assert "sk-probe-leak" not in str(caught.value)
    assert "invalid api key" not in str(caught.value)
    assert caught.value.details == []


def test_non_object_or_invalid_json_completion_maps_to_502():
    for content in ("not json at all", "[1,2]"):
        gateway = DeepSeekGateway(
            api_key="secret",
            client=FakeClient(FakeResponse({"choices": [{"message": {"content": content}}]})),
        )
        with pytest.raises(DeepSeekUnavailable):
            asyncio.run(gateway.json_completion(({"role": "user", "content": "test"},)))


def test_stream_timeout_before_first_content_maps_to_504():
    client = FakeClient(httpx.ReadTimeout("slow upstream"), httpx.ReadTimeout("slow upstream"))
    gateway = DeepSeekGateway(api_key="secret", client=client, max_pre_output_attempts=2)

    async def collect():
        return [chunk async for chunk in gateway.stream_text(({"role": "user", "content": "x"},))]

    with pytest.raises(DeepSeekTimeout) as caught:
        asyncio.run(collect())
    assert caught.value.status_code == 504
    assert "slow upstream" not in str(caught.value)
