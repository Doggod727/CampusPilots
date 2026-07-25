from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.community.models import CampusEvent, Comment, LostFoundItem, Post
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.moderation_handlers import ModerationHandlerRegistry


class ModerationTargetConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="MODERATION_TARGET_CONFLICT",
                         message="审核案件与目标状态不匹配")


class CommunityModerationHandler:
    def __init__(self, model: type[Post] | type[Comment] | type[CampusEvent] | type[LostFoundItem]) -> None:
        self._model = model

    async def approve(
        self, *, session: AsyncSession, case_id: UUID, target_id: UUID,
        reason: str, actor: AuthenticatedUser,
    ) -> None:
        await self._apply(session=session, case_id=case_id, target_id=target_id,
                          decision="approved")

    async def reject(
        self, *, session: AsyncSession, case_id: UUID, target_id: UUID,
        reason: str, actor: AuthenticatedUser,
    ) -> None:
        await self._apply(session=session, case_id=case_id, target_id=target_id,
                          decision="rejected")

    async def escalate(
        self, *, session: AsyncSession, case_id: UUID, target_id: UUID,
        reason: str, actor: AuthenticatedUser,
    ) -> None:
        await self._apply(session=session, case_id=case_id, target_id=target_id,
                          decision="escalated")

    async def _apply(
        self, *, session: AsyncSession, case_id: UUID, target_id: UUID, decision: str,
    ) -> None:
        item = (await session.execute(
            select(self._model).where(self._model.id == target_id).with_for_update()
        )).scalar_one_or_none()
        if item is None or item.moderation_case_id != case_id:
            raise ModerationTargetConflict()
        if item.deleted_at is not None or item.status == "deleted" or decision == "escalated":
            return
        was_published = item.status == "published"
        if decision == "approved":
            item.status = "published"
            if not was_published:
                item.published_at = datetime.now(UTC)
        else:
            item.status = "rejected"
            item.published_at = None
        is_published = item.status == "published"
        if isinstance(item, Comment) and was_published != is_published:
            delta = 1 if is_published else -1
            value = Post.comment_count + delta if delta > 0 else func.greatest(Post.comment_count + delta, 0)
            await session.execute(
                update(Post).where(Post.id == item.post_id).values(comment_count=value)
            )
        item.updated_at = datetime.now(UTC)
        await session.flush()


def register_community_handlers(registry: ModerationHandlerRegistry) -> None:
    for target_type, model in (
        ("post", Post), ("comment", Comment),
        ("event", CampusEvent), ("lost_found", LostFoundItem),
    ):
        registry.register(
            target_module="community", target_type=target_type,
            handler=CommunityModerationHandler(model),
        )
