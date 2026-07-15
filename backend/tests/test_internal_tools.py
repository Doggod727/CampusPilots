from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.agent_platform.internal_auth import (
    InternalServicePrincipal,
    get_internal_service_principal,
)
from app.modules.agent_platform.internal_tools import (
    ApprovalData,
    ToolInvokeData,
    get_internal_tool_service,
)
from app.modules.agent_platform.tool_gateway.errors import ToolForbidden


class FakeInternalToolService:
    def __init__(self, *, awaiting: bool = False, error=None) -> None:
        self.awaiting = awaiting
        self.error = error
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        call_id = uuid4()
        if not self.awaiting:
            return 200, ToolInvokeData(
                tool_call_id=call_id,
                status="succeeded",
                result={"status": "ok"},
            )
        now = datetime.now(UTC)
        approval = ApprovalData(
            id=uuid4(),
            run_id=kwargs["payload"].run_id,
            tool_name=kwargs["tool_name"],
            argument_summary={"safe": True},
            argument_hash="a" * 64,
            status="pending",
            expires_at=now + timedelta(minutes=10),
            decided_at=None,
            created_at=now,
        )
        return 202, ToolInvokeData(
            tool_call_id=call_id,
            status="awaiting_approval",
            approval=approval,
        )


def _client(service):
    app = create_app()
    app.dependency_overrides[get_internal_service_principal] = lambda: InternalServicePrincipal()
    app.dependency_overrides[get_internal_tool_service] = lambda: service
    return TestClient(app)


def _payload():
    return {
        "run_id": str(uuid4()),
        "step_id": str(uuid4()),
        "agent_code": "service_agent",
        "user_id": str(uuid4()),
        "arguments": {"query": "校历"},
    }


def test_internal_tool_success_uses_service_identity_and_request_id():
    service = FakeInternalToolService()
    response = _client(service).post(
        "/internal/v1/tools/service.get_guide:invoke",
        json=_payload(),
        headers={"Idempotency-Key": "invoke-key-0001", "X-Request-Id": "internal-request-001"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "succeeded"
    assert response.json()["request_id"] == response.headers["X-Request-Id"] == "internal-request-001"
    assert service.calls[0]["idempotency_key"] == "invoke-key-0001"


def test_internal_tool_can_return_awaiting_approval_without_sensitive_arguments():
    service = FakeInternalToolService(awaiting=True)
    response = _client(service).post(
        "/internal/v1/tools/work_order.create:invoke",
        json=_payload(),
        headers={"Idempotency-Key": "invoke-key-0002"},
    )
    assert response.status_code == 202
    data = response.json()["data"]
    assert data["status"] == "awaiting_approval"
    assert data["approval"]["argument_summary"] == {"safe": True}
    assert "arguments" not in data


def test_internal_tool_maps_domain_error_to_uniform_envelope():
    response = _client(FakeInternalToolService(error=ToolForbidden())).post(
        "/internal/v1/tools/knowledge.search:invoke",
        json=_payload(),
        headers={"Idempotency-Key": "invoke-key-0003", "X-Request-Id": "internal-request-003"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "TOOL_FORBIDDEN"
    assert response.json()["request_id"] == response.headers["X-Request-Id"]


def test_internal_tool_request_is_strict_and_idempotency_key_is_required():
    payload = _payload()
    payload["unexpected"] = "secret"
    response = _client(FakeInternalToolService()).post(
        "/internal/v1/tools/knowledge.search:invoke", json=payload
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "secret" not in response.text
