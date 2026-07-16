from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.community.errors import PostNotFound
from app.modules.community.models import Post, Topic
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import PostRepository
from app.modules.community.topics import TopicData, topic_data
from app.modules.platform.auth import AuthenticatedUser


@dataclass(frozen=True)
class PublicAuthorData:
    user_id: UUID | None
    display_name: str
    avatar_url: str | None
    is_anonymous: bool


@dataclass(frozen=True)
class PostInteractionData:
    liked: bool
    favorited: bool


@dataclass(frozen=True)
class PostData:
    id: UUID
    topic: TopicData
    author: PublicAuthorData
    title: str
    content_markdown: str
    is_anonymous: bool
    status: str
    moderation_case_id: UUID | None
    like_count: int
    favorite_count: int
    comment_count: int
    report_count: int
    interaction: PostInteractionData
    published_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PostPageData:
    items: tuple[PostData, ...]
    page: int
    page_size: int
    total: int


class PostQueryService:
    def __init__(self, repository: PostRepository, profiles: PublicUserProfilePort) -> None:
        self._repository = repository
        self._profiles = profiles

    async def list(
        self, *, actor: AuthenticatedUser, page: int, page_size: int,
        topic_id: UUID | None = None, q: str | None = None, mine: bool = False,
        sort: str = "-published_at",
    ) -> PostPageData:
        result = await self._repository.list(
            user_id=actor.user_id, mine=mine, topic_id=topic_id, q=q,
            sort=sort, page=page, page_size=page_size,
        )
        items = await self._hydrate(actor, result.items)
        return PostPageData(items, page, page_size, result.total)

    async def get(self, *, actor: AuthenticatedUser, post_id: UUID) -> PostData:
        item = await self._repository.get_visible(
            post_id=post_id, user_id=actor.user_id,
            moderator="community:moderate" in actor.permissions,
        )
        if item is None:
            raise PostNotFound()
        return (await self._hydrate(actor, (item,)))[0]

    async def _hydrate(
        self, actor: AuthenticatedUser, posts: tuple[Post, ...],
    ) -> tuple[PostData, ...]:
        if not posts:
            return ()
        topic_map = await self._repository.topics_by_ids({item.topic_id for item in posts})
        if len(topic_map) != len({item.topic_id for item in posts}):
            raise PostNotFound()
        interactions = await self._repository.interaction_states(
            post_ids={item.id for item in posts}, user_id=actor.user_id,
        )
        author_ids = {item.author_user_id for item in posts if not item.is_anonymous}
        profiles = await self._profiles.get_many(author_ids) if author_ids else {}
        return tuple(
            self._post_data(item, topic_map[item.topic_id], interactions.get(item.id, set()),
                            profiles.get(item.author_user_id), actor)
            for item in posts
        )

    @staticmethod
    def _post_data(item: Post, topic: Topic, reactions: set[str], profile, actor) -> PostData:
        if item.is_anonymous:
            author = PublicAuthorData(None, "匿名同学", None, True)
        else:
            author = PublicAuthorData(
                item.author_user_id,
                profile.display_name if profile is not None else "已注销用户",
                profile.avatar_url if profile is not None else None,
                False,
            )
        privileged = item.author_user_id == actor.user_id or "community:moderate" in actor.permissions
        return PostData(
            id=item.id, topic=topic_data(topic), author=author, title=item.title,
            content_markdown=item.content_markdown, is_anonymous=item.is_anonymous,
            status=item.status,
            moderation_case_id=item.moderation_case_id if privileged else None,
            like_count=item.like_count, favorite_count=item.favorite_count,
            comment_count=item.comment_count, report_count=item.report_count,
            interaction=PostInteractionData("like" in reactions, "favorite" in reactions),
            published_at=item.published_at, version=item.version,
            created_at=item.created_at, updated_at=item.updated_at,
        )
