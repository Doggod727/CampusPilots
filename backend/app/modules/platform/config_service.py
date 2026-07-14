from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.models import AppConfig
from app.modules.platform.repositories import AuditLogRepository, ConfigRepository

SENSITIVE_CONFIG_PARTS = ("password", "secret", "token", "api_key", "apikey", "database_url", "redis_url")


class ConfigNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="CONFIG_NOT_FOUND", message="配置不存在")


class ConfigNotEditable(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=403, code="CONFIG_NOT_EDITABLE", message="配置不可编辑")


class InvalidConfigValue(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="INVALID_CONFIG_VALUE", message="配置值类型无效")


class ConfigVersionConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="RESOURCE_VERSION_CONFLICT", message="数据已被其他操作更新，请刷新后重试")


def is_sensitive_config_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_CONFIG_PARTS)


def validate_config_value(value: object, value_type: str) -> None:
    valid = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "json": True,
    }.get(value_type, False)
    if not valid:
        raise InvalidConfigValue()
    if value_type == "json":
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise InvalidConfigValue() from None


class ConfigService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ConfigRepository,
        audit_service: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit_service
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def list(self, namespace: str | None = None) -> list[AppConfig]:
        return [
            item for item in await self._repository.list(namespace)
            if not is_sensitive_config_key(item.key)
        ]

    async def update(
        self, *, actor: AuthenticatedUser, key: str, value: object,
        expected_version: int, request_id: str,
    ) -> AppConfig:
        if is_sensitive_config_key(key):
            raise ConfigNotFound()
        async with self._session.begin():
            config = await self._repository.get_by_key(key)
            if config is None:
                raise ConfigNotFound()
            if not config.editable:
                raise ConfigNotEditable()
            validate_config_value(value, config.value_type)
            if config.version != expected_version:
                raise ConfigVersionConflict()
            now = self._current_time()
            if not await self._repository.update_if_version(
                key=key, expected_version=expected_version, value=value,
                updated_by=actor.user_id, updated_at=now,
            ):
                raise ConfigVersionConflict()
            before = {"key": config.key, "value": config.value, "version": config.version}
            config.value = value
            config.version = expected_version + 1
            config.updated_by = actor.user_id
            config.updated_at = now
            self._audit.record_success(
                action="config.update", resource_type="app_config", resource_id=key,
                request_id=request_id, actor_user_id=actor.user_id,
                actor_username=actor.username, before_data=before,
                after_data={"key": key, "value": value, "version": config.version},
            )
            return config

    def _current_time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@asynccontextmanager
async def config_service_context(settings: Settings) -> AsyncIterator[ConfigService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield ConfigService(
                session=session, repository=ConfigRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()
