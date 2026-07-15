import pytest
from pydantic import ValidationError
from pathlib import Path

from app.core.config import Settings

REQUIRED_ENVIRONMENT = {
    "DATABASE_URL": "postgresql+asyncpg://user:password@localhost:5432/campuspilot",
    "REDIS_URL": "redis://localhost:6379/0",
    "JWT_SECRET": "unit-test-jwt-secret",
    "FRONTEND_ORIGIN": "http://localhost:5173",
    "DEEPSEEK_API_KEY": "unit-test-deepseek-key",
}


def set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def test_settings_require_external_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in REQUIRED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)  # type: ignore[call-arg]

    missing_fields = {item["loc"][0] for item in error.value.errors()}
    assert missing_fields == {
        "database_url",
        "redis_url",
        "jwt_secret",
        "frontend_origin",
        "deepseek_api_key",
    }


def test_settings_load_defaults_and_mask_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.access_token_minutes == 15
    assert settings.refresh_token_days == 7
    assert settings.refresh_cookie_secure is False
    assert str(settings.deepseek_base_url) == "https://api.deepseek.com/"
    assert settings.deepseek_model == "deepseek-v4-pro"
    assert settings.use_mock_campus_adapters is True
    assert settings.local_router_model_path == Path("/models/router")
    assert settings.local_router_confidence == 0.80
    assert settings.local_router_timeout_ms == 500
    assert settings.reranker_model_path == Path("/models/reranker")
    assert settings.reranker_enabled is False
    assert settings.reranker_timeout_ms == 1000
    assert settings.agent_max_steps == 6
    assert settings.agent_max_specialists == 3
    assert settings.agent_parallelism == 3
    assert settings.agent_run_timeout_seconds == 120
    assert settings.approval_ttl_seconds == 600
    assert settings.tool_default_timeout_ms == 10000
    assert settings.mcp_enabled is False
    assert settings.model_artifact_root == Path("/data/models")
    assert settings.dataset_artifact_root == Path("/data/datasets")
    assert settings.training_gpu_enabled is False

    rendered = f"{settings!r}\n{settings.model_dump_json()}"
    assert REQUIRED_ENVIRONMENT["JWT_SECRET"] not in rendered
    assert REQUIRED_ENVIRONMENT["DEEPSEEK_API_KEY"] not in rendered


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("ACCESS_TOKEN_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_DAYS", "14")
    monkeypatch.setenv("REFRESH_COOKIE_SECURE", "true")
    monkeypatch.setenv("DEEPSEEK_MODEL", "mock-deepseek-model")
    monkeypatch.setenv("USE_MOCK_CAMPUS_ADAPTERS", "false")
    monkeypatch.setenv("LOCAL_ROUTER_CONFIDENCE", "0.75")
    monkeypatch.setenv("LOCAL_ROUTER_TIMEOUT_MS", "750")
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("AGENT_MAX_STEPS", "4")
    monkeypatch.setenv("AGENT_MAX_SPECIALISTS", "2")
    monkeypatch.setenv("AGENT_PARALLELISM", "1")
    monkeypatch.setenv("AGENT_RUN_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("APPROVAL_TTL_SECONDS", "300")
    monkeypatch.setenv("TOOL_DEFAULT_TIMEOUT_MS", "2500")
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("TRAINING_GPU_ENABLED", "true")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.access_token_minutes == 30
    assert settings.refresh_token_days == 14
    assert settings.refresh_cookie_secure is True
    assert settings.deepseek_model == "mock-deepseek-model"
    assert settings.use_mock_campus_adapters is False
    assert settings.local_router_confidence == 0.75
    assert settings.local_router_timeout_ms == 750
    assert settings.reranker_enabled is True
    assert settings.agent_max_steps == 4
    assert settings.agent_max_specialists == 2
    assert settings.agent_parallelism == 1
    assert settings.agent_run_timeout_seconds == 90
    assert settings.approval_ttl_seconds == 300
    assert settings.tool_default_timeout_ms == 2500
    assert settings.mcp_enabled is True
    assert settings.training_gpu_enabled is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ACCESS_TOKEN_MINUTES", "0"),
        ("REFRESH_TOKEN_DAYS", "-1"),
    ],
)
def test_settings_reject_non_positive_token_duration(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOCAL_ROUTER_CONFIDENCE", "1.1"),
        ("LOCAL_ROUTER_TIMEOUT_MS", "0"),
        ("RERANKER_TIMEOUT_MS", "60001"),
        ("AGENT_MAX_STEPS", "7"),
        ("AGENT_MAX_SPECIALISTS", "4"),
        ("AGENT_PARALLELISM", "4"),
        ("AGENT_RUN_TIMEOUT_SECONDS", "0"),
        ("APPROVAL_TTL_SECONDS", "3601"),
        ("TOOL_DEFAULT_TIMEOUT_MS", "99"),
    ],
)
def test_settings_reject_invalid_agent_runtime_limits(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
