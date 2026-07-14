from fastapi.testclient import TestClient

from app.main import create_app


def test_cors_preflight_allows_configured_origin_and_headers(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://campus.example/")
    client = TestClient(create_app())
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://campus.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type, X-Request-Id, Idempotency-Key",
            "X-Request-Id": "cors-preflight",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://campus.example"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Authorization" in response.headers["access-control-allow-headers"]
    assert response.headers["X-Request-Id"] == "cors-preflight"


def test_cors_rejects_unconfigured_origin() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/health/live",
        headers={"Origin": "https://evil.example", "X-Request-Id": "cors-denied"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert response.headers["X-Request-Id"] == "cors-denied"


def test_cors_allows_configured_origin_on_success_response(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173/")
    response = TestClient(create_app()).get(
        "/health/live", headers={"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
