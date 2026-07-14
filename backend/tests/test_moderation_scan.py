import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.platform.models import SensitiveWord
from app.modules.platform.moderation_scan import (
    InvalidSensitiveWordRule,
    SensitiveWordScanner,
)


def _rule(word: str, action: str, match_type: str = "contains", replacement: str | None = "***") -> SensitiveWord:
    return SensitiveWord(
        id=uuid4(), word=word, match_type=match_type, action=action,
        replacement=replacement, scope="user_input", enabled=True,
    )


def test_scanner_applies_mask_and_hides_matched_text() -> None:
    repository = MagicMock()
    repository.list_enabled_for_scope = AsyncMock(
        return_value=[_rule("秘密", "mask")]
    )

    result = asyncio.run(SensitiveWordScanner(repository).scan(scope="user_input", text="这是秘密"))

    assert result.action == "mask"
    assert result.risk_level == "medium"
    assert result.sanitized_text == "这是***"
    assert result.hits[0].matched_text is None
    assert "秘密" not in repr(result.hits)


def test_scanner_uses_deterministic_action_priority_and_does_not_expose_blocked_text() -> None:
    repository = MagicMock()
    repository.list_enabled_for_scope = AsyncMock(
        return_value=[_rule("bad", "mask"), _rule("bad", "block")]
    )

    result = asyncio.run(SensitiveWordScanner(repository).scan(scope="community", text="bad"))

    assert result.action == "block"
    assert result.risk_level == "critical"
    assert result.sanitized_text == "bad"
    assert all(hit.matched_text is None for hit in result.hits)


def test_scanner_supports_exact_and_regex_and_rejects_invalid_regex() -> None:
    repository = MagicMock()
    repository.list_enabled_for_scope = AsyncMock(
        side_effect=[
            [_rule("secret", "review", "exact")],
            [_rule("secret", "review", "exact")],
            [_rule(r"secret\d+", "block", "regex")],
            [_rule(r"[", "block", "regex")],
        ]
    )
    scanner = SensitiveWordScanner(repository)

    exact = asyncio.run(scanner.scan(scope="user_input", text="secret"))
    assert exact.action == "review"
    no_match = asyncio.run(scanner.scan(scope="user_input", text="secret value"))
    assert no_match.action == "allow"
    regex = asyncio.run(scanner.scan(scope="user_input", text="secret42"))
    assert regex.action == "block"


def test_invalid_regex_raises_safe_domain_error() -> None:
    repository = MagicMock()
    repository.list_enabled_for_scope = AsyncMock(return_value=[_rule(r"[", "block", "regex")])

    try:
        asyncio.run(SensitiveWordScanner(repository).scan(scope="user_input", text="x"))
    except InvalidSensitiveWordRule as error:
        assert "[" not in str(error)
    else:
        raise AssertionError("invalid regex must be rejected")
