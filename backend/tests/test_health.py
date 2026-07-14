from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_liveness_generates_request_id_and_returns_contract() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "OK"
    assert payload["message"] == "success"
    assert payload["data"] == {"status": "alive"}
    assert payload["request_id"] == response.headers["X-Request-Id"]
    assert 8 <= len(payload["request_id"]) <= 64
    assert datetime.fromisoformat(payload["timestamp"]).tzinfo is not None


def test_liveness_preserves_valid_request_id() -> None:
    request_id = "client-request-123"

    response = client.get("/health/live", headers={"X-Request-Id": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == request_id
    assert response.json()["request_id"] == request_id


def test_liveness_replaces_invalid_request_id() -> None:
    for invalid_request_id in ("short", "x" * 65):
        response = client.get(
            "/health/live",
            headers={"X-Request-Id": invalid_request_id},
        )

        generated_request_id = response.headers["X-Request-Id"]
        assert response.status_code == 200
        assert generated_request_id != invalid_request_id
        assert response.json()["request_id"] == generated_request_id
        assert 8 <= len(generated_request_id) <= 64
