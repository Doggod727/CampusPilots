import asyncio

import httpx
import pytest

from app.modules.agent_platform.deepseek import DeepSeekGateway, DeepSeekTimeout
from app.scripts import deepseek_provider_fault_probe as probe


class _FakeResponse:
    def json(self):
        return {"choices": [{"message": {"content": "{\"answer\": \"ok\"}"}}]}

    def raise_for_status(self):
        return None


class _FakeClient:
    async def post(self, *_args, **_kwargs):
        return _FakeResponse()


def test_assert_no_leak_accepts_clean_text():
    probe.assert_no_leak("stable public summary", stage="UNIT")


def test_assert_no_leak_rejects_key_and_upstream_markers():
    for rendered in (probe.PROBE_KEY, f"body: {probe.DELAY_BODY_MARKER}", "Authentication Fails, your key"):
        with pytest.raises(probe.ProbeFailure):
            probe.assert_no_leak(rendered, stage="UNIT")


def test_matrix_cases_cover_all_entries_and_expected_codes():
    assert {case.name for case in probe.MATRIX_CASES} == {
        "rejected_credentials",
        "unreachable_endpoint",
        "delayed_upstream",
    }
    assert [case.expected_status for case in probe.MATRIX_CASES] == [502, 502, 504]
    assert set(probe.GATEWAY_ENTRIES) == {"router", "specialist", "rag_answer", "sse_stream"}


def test_expect_provider_error_rejects_unexpected_success():
    gateway = DeepSeekGateway(api_key="sk-test", client=_FakeClient())
    with pytest.raises(probe.ProbeFailure) as caught:
        asyncio.run(probe.expect_provider_error("rag_answer", probe.MATRIX_CASES[1], gateway))
    assert "NOERROR" in caught.value.code


def test_expect_provider_error_accepts_unreachable_502():
    port = probe._free_loopback_port()
    gateway = DeepSeekGateway(api_key="sk-test", base_url=f"http://127.0.0.1:{port}")
    asyncio.run(probe.expect_provider_error("rag_answer", probe.MATRIX_CASES[1], gateway))


def test_slow_upstream_server_delays_and_gateway_maps_504():
    async def run():
        async with probe.SlowUpstreamServer(delay_seconds=2.0) as server:
            gateway = DeepSeekGateway(
                api_key="sk-test", base_url=f"http://127.0.0.1:{server.port}", timeout_seconds=1
            )
            with pytest.raises(DeepSeekTimeout):
                await gateway.json_completion(({"role": "user", "content": "x"},))

    asyncio.run(run())
