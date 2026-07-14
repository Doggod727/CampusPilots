import asyncio
from uuid import uuid4

import pytest

from app.modules.platform.moderation_handlers import (
    ModerationHandlerRegistry,
    ModerationHandlerUnavailable,
)


class _Handler:
    async def approve(self, **kwargs):
        return None

    async def reject(self, **kwargs):
        return None

    async def escalate(self, **kwargs):
        return None


def test_registry_resolves_registered_handler_and_rejects_duplicates() -> None:
    registry = ModerationHandlerRegistry()
    handler = _Handler()
    registry.register(target_module="community", target_type="post", handler=handler)
    assert registry.resolve(target_module="community", target_type="post") is handler
    with pytest.raises(ValueError):
        registry.register(target_module="community", target_type="post", handler=handler)


def test_registry_missing_handler_is_safe_domain_error() -> None:
    registry = ModerationHandlerRegistry()
    with pytest.raises(ModerationHandlerUnavailable) as error:
        registry.resolve(target_module="community", target_type="comment")
    assert "community" not in str(error.value)
    assert "comment" not in str(error.value)


def test_registered_handler_protocol_supports_decision_methods() -> None:
    registry = ModerationHandlerRegistry()
    handler = _Handler()
    registry.register(target_module="community", target_type="post", handler=handler)
    resolved = registry.resolve(target_module="community", target_type="post")
    asyncio.run(resolved.approve(case_id=uuid4(), target_id=uuid4(), reason="ok", actor=None))
    asyncio.run(resolved.reject(case_id=uuid4(), target_id=uuid4(), reason="no", actor=None))
    asyncio.run(resolved.escalate(case_id=uuid4(), target_id=uuid4(), reason="more", actor=None))
