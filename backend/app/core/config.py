from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    redis_url: str
    jwt_secret: SecretStr
    frontend_origin: AnyHttpUrl
    deepseek_api_key: SecretStr
    internal_tool_secret: SecretStr | None = None

    access_token_minutes: int = Field(default=15, gt=0)
    refresh_token_days: int = Field(default=7, gt=0)
    refresh_cookie_secure: bool = False
    deepseek_base_url: AnyHttpUrl = AnyHttpUrl("https://api.deepseek.com")
    deepseek_model: str = "deepseek-v4-pro"
    use_mock_campus_adapters: bool = True

    local_router_model_path: Path = Path("/models/router")
    local_router_confidence: float = Field(default=0.80, ge=0, le=1)
    local_router_timeout_ms: int = Field(default=500, gt=0, le=60000)
    reranker_model_path: Path = Path("/models/reranker")
    reranker_enabled: bool = False
    reranker_timeout_ms: int = Field(default=1000, gt=0, le=60000)

    agent_max_steps: int = Field(default=6, ge=1, le=6)
    agent_max_specialists: int = Field(default=3, ge=1, le=3)
    agent_parallelism: int = Field(default=3, ge=1, le=3)
    agent_run_timeout_seconds: int = Field(default=120, gt=0, le=3600)
    agent_checkpoint_secret: SecretStr | None = None
    agent_checkpoint_ttl_seconds: int = Field(default=3600, gt=0, le=86400)
    agent_runtime_max_attempts: int = Field(default=3, ge=1, le=10)
    agent_runtime_claim_timeout_seconds: int = Field(default=60, gt=0, le=3600)
    agent_runtime_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    approval_ttl_seconds: int = Field(default=600, gt=0, le=3600)
    tool_default_timeout_ms: int = Field(default=10000, ge=100, le=60000)
    mcp_enabled: bool = False

    model_artifact_root: Path = Path("/data/models")
    dataset_artifact_root: Path = Path("/data/datasets")
    dataset_upload_ttl_seconds: int = Field(default=3600, gt=0, le=86400)
    training_gpu_enabled: bool = False
    local_training_base_models: str = "Qwen/Qwen2.5-1.5B-Instruct"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
