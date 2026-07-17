import asyncio

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.platform import readiness


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        jwt_secret="secret",
        frontend_origin="http://localhost:5173",
        deepseek_api_key="key",
    )


def test_check_readiness_reports_all_dependencies() -> None:
    async def up(*args):
        return readiness.DependencyStatus(status="up", latency_ms=1)

    data = asyncio.run(
        readiness.check_readiness(
            _settings(), postgres=up, redis=up, chroma=up
        )
    )
    assert data.status == "ready"
    assert data.dependencies.postgres.status == "up"


def test_check_readiness_reports_not_ready_without_details() -> None:
    async def down(*args):
        return readiness.DependencyStatus(
            status="down", latency_ms=2, message="redis unavailable"
        )

    async def up(*args):
        return readiness.DependencyStatus(status="up", latency_ms=1)

    data = asyncio.run(
        readiness.check_readiness(
            _settings(), postgres=up, redis=down, chroma=up
        )
    )
    assert data.status == "not_ready"
    assert data.dependencies.redis.status == "down"


def test_readiness_missing_configuration_is_safe_503(monkeypatch) -> None:
    monkeypatch.setattr(readiness, "get_settings", lambda: (_ for _ in ()).throw(ValueError("DATABASE_URL=secret")))
    response = TestClient(create_app()).get(
        "/health/ready", headers={"X-Request-Id": "ready-test-id"}
    )
    assert response.status_code == 503
    assert response.headers["X-Request-Id"] == "ready-test-id"
    assert response.json()["code"] == "SERVICE_NOT_READY"
    assert "DATABASE_URL" not in response.text
    assert "secret" not in response.text


def test_readiness_dependency_failure_is_safe_503(monkeypatch) -> None:
    async def fake_check(settings):
        return readiness.ReadinessData(
            status="not_ready",
            dependencies=readiness.ReadinessDependencies(
                postgres=readiness.DependencyStatus(status="down", latency_ms=1),
                redis=readiness.DependencyStatus(status="up", latency_ms=1),
                chroma=readiness.DependencyStatus(status="up", latency_ms=0),
            ),
        )

    monkeypatch.setattr(readiness, "get_settings", _settings)
    monkeypatch.setattr(readiness, "check_readiness", fake_check)
    response = TestClient(create_app()).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_NOT_READY"


def test_liveness_does_not_require_configuration(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = TestClient(create_app()).get("/health/live")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "alive"


def test_chroma_probe_fails_closed_without_leaking_path(tmp_path) -> None:
    settings = _settings().model_copy(update={"knowledge_chroma_path": tmp_path / "private"})
    result = asyncio.run(readiness.probe_chroma(settings))

    # The minimal test environment intentionally omits the optional AI dependency.
    # A full deployment installs it; either way the probe exposes no local path.
    assert result.status in {"up", "down"}
    if result.status == "down":
        assert result.message == "chroma unavailable"
    assert str(tmp_path) not in str(result)
