from dataclasses import dataclass
import re
from uuid import UUID

from app.core.errors import AppError
from app.modules.platform.models import SensitiveWord
from app.modules.platform.repositories import SensitiveWordRepository

POLICY_VERSION = "m4-sensitive-v1"
_ACTION_PRIORITY = {"allow": 0, "mask": 1, "review": 2, "block": 3}
_RISK_BY_ACTION = {
    "allow": "low",
    "mask": "medium",
    "review": "high",
    "block": "critical",
}


class InvalidSensitiveWordRule(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=422,
            code="INVALID_SENSITIVE_WORD_RULE",
            message="敏感词规则无效",
        )


@dataclass(frozen=True)
class ScanHit:
    rule: str
    action: str
    matched_text: None = None


@dataclass(frozen=True)
class ScanResult:
    action: str
    risk_level: str
    hits: tuple[ScanHit, ...]
    policy_version: str
    sanitized_text: str


def validate_rule(rule: SensitiveWord) -> None:
    if rule.match_type == "regex":
        try:
            re.compile(rule.word)
        except re.error:
            raise InvalidSensitiveWordRule() from None


class SensitiveWordScanner:
    """Deterministic, non-logging sensitive-word scanner."""

    def __init__(self, repository: SensitiveWordRepository) -> None:
        self._repository = repository

    async def scan(self, *, scope: str, text: str) -> ScanResult:
        rules = await self._repository.list_enabled_for_scope(scope)
        source = text
        sanitized = text
        matched: list[tuple[SensitiveWord, str]] = []
        for rule in rules:
            validate_rule(rule)
            if rule.match_type == "exact":
                is_match = source.casefold() == rule.word.casefold()
            elif rule.match_type == "contains":
                is_match = rule.word.casefold() in source.casefold()
            else:
                is_match = re.search(rule.word, source, flags=re.IGNORECASE) is not None
            if is_match:
                matched.append((rule, rule.action))
                if rule.action == "mask":
                    replacement = rule.replacement or "***"
                    if rule.match_type == "regex":
                        sanitized = re.sub(
                            rule.word, replacement, sanitized, flags=re.IGNORECASE
                        )
                    else:
                        pattern = re.escape(rule.word)
                        sanitized = re.sub(
                            pattern, replacement, sanitized, flags=re.IGNORECASE
                        )
        matched.sort(key=lambda item: (_ACTION_PRIORITY[item[1]], str(item[0].id)))
        action = max(
            (item[1] for item in matched),
            key=lambda value: _ACTION_PRIORITY[value],
            default="allow",
        )
        return ScanResult(
            action=action,
            risk_level=_RISK_BY_ACTION[action],
            hits=tuple(
                ScanHit(rule=str(rule.id), action=rule_action)
                for rule, rule_action in matched
            ),
            policy_version=POLICY_VERSION,
            sanitized_text=sanitized if action == "mask" else text,
        )
