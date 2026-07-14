import pytest
from pydantic import ValidationError

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

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.access_token_minutes == 30
    assert settings.refresh_token_days == 14
    assert settings.refresh_cookie_secure is True
    assert settings.deepseek_model == "mock-deepseek-model"
    assert settings.use_mock_campus_adapters is False


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
