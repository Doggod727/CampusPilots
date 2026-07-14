from datetime import datetime

from fastapi import Query
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.main import create_app
from app.shared.responses import ErrorDetail

error_app = create_app()


@error_app.get("/_test/domain-error", include_in_schema=False)
async def raise_domain_error() -> None:
    raise AppError(
        status_code=409,
        code="RESOURCE_VERSION_CONFLICT",
        message="数据已被其他操作更新，请刷新后重试",
        details=[ErrorDetail(field="version", reason="expected=2, actual=3")],
        headers={"Retry-After": "1"},
    )


@error_app.get("/_test/validation-error", include_in_schema=False)
async def validate_query(value: int = Query(ge=1)) -> dict[str, int]:
    return {"value": value}


@error_app.get("/_test/unhandled-error", include_in_schema=False)
async def raise_unhandled_error() -> None:
    raise RuntimeError("database password=do-not-expose")


client = TestClient(error_app, raise_server_exceptions=False)


def assert_error_contract(response_request_id: str, payload: dict[str, object]) -> None:
    assert set(payload) == {"code", "message", "details", "request_id", "timestamp"}
    assert payload["request_id"] == response_request_id
    assert isinstance(payload["details"], list)
    assert datetime.fromisoformat(str(payload["timestamp"])).tzinfo is not None


def test_app_error_preserves_domain_contract_and_headers() -> None:
    request_id = "domain-error-request"

    response = client.get(
        "/_test/domain-error",
        headers={"X-Request-Id": request_id},
    )

    assert response.status_code == 409
    assert response.headers["X-Request-Id"] == request_id
    assert response.headers["Retry-After"] == "1"
    payload = response.json()
    assert_error_contract(request_id, payload)
    assert payload["code"] == "RESOURCE_VERSION_CONFLICT"
    assert payload["details"] == [
        {"field": "version", "reason": "expected=2, actual=3"}
    ]


def test_validation_error_uses_safe_flat_envelope() -> None:
    request_id = "validation-error-request"
    unsafe_input = "secret-input-value"

    response = client.get(
        "/_test/validation-error",
        params={"value": unsafe_input},
        headers={"X-Request-Id": request_id},
    )

    assert response.status_code == 422
    assert response.headers["X-Request-Id"] == request_id
    payload = response.json()
    assert_error_contract(request_id, payload)
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["details"][0]["field"] == "value"
    assert unsafe_input not in response.text


def test_unknown_route_uses_http_error_envelope() -> None:
    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    request_id = response.headers["X-Request-Id"]
    payload = response.json()
    assert_error_contract(request_id, payload)
    assert payload["code"] == "NOT_FOUND"
    assert payload["details"] == []


def test_unhandled_error_hides_internal_details() -> None:
    request_id = "unhandled-error-request"

    response = client.get(
        "/_test/unhandled-error",
        headers={"X-Request-Id": request_id},
    )

    assert response.status_code == 500
    assert response.headers["X-Request-Id"] == request_id
    payload = response.json()
    assert_error_contract(request_id, payload)
    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["message"] == "服务器内部错误"
    assert "database password" not in response.text
    assert "do-not-expose" not in response.text
