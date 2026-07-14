from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.platform.models import AppConfig
from app.shared.responses import SuccessResponse


class ConfigData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    namespace: str
    value: Any
    value_type: str
    description: str | None
    editable: bool
    version: int = Field(ge=1)
    updated_at: datetime
    updated_by: str | None


class ConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any
    version: int = Field(ge=1)


class ConfigListData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ConfigData]


ConfigResponse = SuccessResponse[ConfigData]
ConfigListResponse = SuccessResponse[ConfigListData]


def config_data(config: AppConfig) -> ConfigData:
    return ConfigData(
        key=config.key, namespace=config.namespace, value=config.value,
        value_type=config.value_type, description=config.description,
        editable=config.editable, version=config.version,
        updated_at=config.updated_at, updated_by=str(config.updated_by) if config.updated_by else None,
    )
