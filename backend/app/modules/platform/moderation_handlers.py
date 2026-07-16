from collections.abc import MutableMapping
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.platform.auth import AuthenticatedUser


class ModerationTargetHandler(Protocol):
    async def approve(
        self, *, session: AsyncSession, case_id: UUID, target_id: UUID,
        reason: str, actor: AuthenticatedUser
    ) -> None: ...

    async def reject(
        self, *, session: AsyncSession, case_id: UUID, target_id: UUID,
        reason: str, actor: AuthenticatedUser
    ) -> None: ...

    async def escalate(
        self, *, session: AsyncSession, case_id: UUID, target_id: UUID,
        reason: str, actor: AuthenticatedUser
    ) -> None: ...


class ModerationHandlerUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="MODERATION_HANDLER_UNAVAILABLE",
            message="审核目标暂不可处理",
        )


class ModerationHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: MutableMapping[tuple[str, str], ModerationTargetHandler] = {}

    def register(
        self, *, target_module: str, target_type: str, handler: ModerationTargetHandler
    ) -> None:
        key = (target_module, target_type)
        if key in self._handlers:
            raise ValueError("moderation handler already registered")
        self._handlers[key] = handler

    def resolve(self, *, target_module: str, target_type: str) -> ModerationTargetHandler:
        try:
            return self._handlers[(target_module, target_type)]
        except KeyError:
            raise ModerationHandlerUnavailable() from None

    def unregister(self, *, target_module: str, target_type: str) -> None:
        self._handlers.pop((target_module, target_type), None)
