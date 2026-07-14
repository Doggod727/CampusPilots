from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.platform.models import SensitiveWord
from app.shared.responses import SuccessResponse

SensitiveScope = Literal["user_input", "ai_output", "community", "all"]
MatchType = Literal["exact", "contains", "regex"]
WordAction = Literal["mask", "block", "review"]


class SensitiveWordData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    word: str
    match_type: MatchType
    action: WordAction
    replacement: str | None
    scope: SensitiveScope
    enabled: bool
    created_at: datetime


class SensitiveWordCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    word: str = Field(min_length=1, max_length=200)
    match_type: MatchType
    action: WordAction
    replacement: str | None = Field(default=None, max_length=100)
    scope: SensitiveScope
    enabled: bool = True

    @model_validator(mode="after")
    def validate_replacement(self) -> "SensitiveWordCreateRequest":
        if self.action == "mask" and self.replacement is None:
            raise ValueError("replacement is required for mask action")
        return self


class SensitiveWordPageData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SensitiveWordData]
    pagination: dict[str, int]


SensitiveWordResponse = SuccessResponse[SensitiveWordData]
SensitiveWordPageResponse = SuccessResponse[SensitiveWordPageData]


def sensitive_word_data(rule: SensitiveWord) -> SensitiveWordData:
    return SensitiveWordData(
        id=rule.id, word=rule.word, match_type=rule.match_type,
        action=rule.action, replacement=rule.replacement, scope=rule.scope,
        enabled=rule.enabled, created_at=rule.created_at,
    )
