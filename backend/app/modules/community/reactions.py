from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import CommunityContentPendingReview, PostNotFound
from app.modules.community.repositories import ReactionRepository
from app.modules.platform.auth import AuthenticatedUser


@dataclass(frozen=True)
class ReactionData:
    post_id: UUID
    reaction_type: str
    active: bool
    like_count: int
    favorite_count: int


class ReactionService:
    def __init__(self, *, session: AsyncSession, repository: ReactionRepository) -> None:
        self._session = session
        self._repository = repository

    async def put(
        self, *, actor: AuthenticatedUser, post_id: UUID, reaction_type: str,
    ) -> ReactionData:
        async with self._session.begin():
            post = await self._repository.get_post_for_update(post_id)
            if post is None:
                raise PostNotFound()
            if post.status != "published":
                privileged = post.author_user_id == actor.user_id or "community:moderate" in actor.permissions
                if privileged and post.status == "pending_review":
                    raise CommunityContentPendingReview()
                raise PostNotFound()
            inserted = await self._repository.insert(
                post_id=post_id, user_id=actor.user_id, reaction_type=reaction_type,
            )
            counts = (post.like_count, post.favorite_count)
            if inserted:
                counts = await self._repository.adjust_count(
                    post_id=post_id, reaction_type=reaction_type, delta=1,
                )
            return ReactionData(post_id, reaction_type, True, *counts)

    async def delete(
        self, *, actor: AuthenticatedUser, post_id: UUID, reaction_type: str,
    ) -> ReactionData:
        async with self._session.begin():
            post = await self._repository.get_post_for_update(post_id)
            if post is None or post.status != "published":
                raise PostNotFound()
            deleted = await self._repository.delete(
                post_id=post_id, user_id=actor.user_id, reaction_type=reaction_type,
            )
            counts = (post.like_count, post.favorite_count)
            if deleted:
                counts = await self._repository.adjust_count(
                    post_id=post_id, reaction_type=reaction_type, delta=-1,
                )
            return ReactionData(post_id, reaction_type, False, *counts)
