import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.community.errors import PostNotFound
from app.modules.community.models import Post, Topic
from app.modules.community.posts import PostQueryService
from app.modules.community.profiles import PublicUserProfile
from app.modules.community.repositories import PostPage, PostRepository
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
USER_ID = UUID("90000000-0000-4000-8000-000000000001")
OTHER_ID = UUID("90000000-0000-4000-8000-000000000002")
TOPIC_ID = UUID("74000000-0000-4000-8000-000000000001")


def _actor(*, moderator=False):
    return AuthenticatedUser(
        user_id=USER_ID, username="student01", display_name="学生一号", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "学生"),),
        permissions=("community:read", "community:moderate") if moderator else ("community:read",),
        last_login_at=None, created_at=NOW, version=1,
    )


def _topic():
    return Topic(
        id=TOPIC_ID, code="campus-life", name="校园生活", description=None,
        allow_anonymous=False, sort_order=10, status="active", created_by=OTHER_ID,
        version=1, created_at=NOW, updated_at=NOW, deleted_at=None,
    )


def _post(*, anonymous=False, owner=OTHER_ID, status="published"):
    return Post(
        id=uuid4(), topic_id=TOPIC_ID, author_user_id=owner, title="测试帖子",
        content_markdown="测试正文", is_anonymous=anonymous, status=status,
        risk_level="low", moderation_case_id=uuid4() if status != "published" else None,
        moderation_policy_version="m4-sensitive-v1", like_count=1, favorite_count=2,
        comment_count=3, report_count=0, published_at=NOW if status == "published" else None,
        version=1, created_at=NOW, updated_at=NOW, deleted_at=None,
    )


def test_repository_public_list_applies_visibility_search_and_stable_order() -> None:
    session = MagicMock()
    rows = MagicMock(); rows.scalars.return_value.all.return_value = []
    count = MagicMock(); count.scalar_one.return_value = 0
    session.execute = AsyncMock(side_effect=[rows, count])
    result = asyncio.run(PostRepository(session).list(
        user_id=USER_ID, mine=False, topic_id=TOPIC_ID, q=" 100%_ ",
        sort="-published_at", page=2, page_size=10,
    ))
    assert result == PostPage((), 0)
    statement = session.execute.await_args_list[0].args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "posts.deleted_at IS NULL" in sql and "posts.status =" in sql
    assert "posts.topic_id =" in sql and "ILIKE" in sql and "ESCAPE" in sql
    assert "ORDER BY coalesce(community.posts.published_at, community.posts.created_at) DESC" in sql
    assert "community.posts.id" in sql and "LIMIT" in sql and "OFFSET" in sql
    assert any("100\\%\\_" in str(value) for value in statement.compile().params.values())


def test_repository_mine_and_detail_visibility_are_enforced_in_sql() -> None:
    session = MagicMock()
    empty = MagicMock(); empty.scalars.return_value.all.return_value = []
    count = MagicMock(); count.scalar_one.return_value = 0
    detail = MagicMock(); detail.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(side_effect=[empty, count, detail])
    repository = PostRepository(session)
    asyncio.run(repository.list(
        user_id=USER_ID, mine=True, topic_id=None, q=None,
        sort="published_at", page=1, page_size=20,
    ))
    asyncio.run(repository.get_visible(post_id=uuid4(), user_id=USER_ID, moderator=False))
    list_sql = str(session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    detail_sql = str(session.execute.await_args_list[2].args[0].compile(dialect=postgresql.dialect()))
    assert "posts.author_user_id =" in list_sql and "posts.status =" not in list_sql
    assert "posts.status =" in detail_sql and "posts.author_user_id =" in detail_sql


def test_query_service_batches_topics_interactions_and_public_profiles() -> None:
    posts = (_post(), _post(owner=USER_ID))
    repo = MagicMock()
    repo.list = AsyncMock(return_value=PostPage(posts, 2))
    repo.topics_by_ids = AsyncMock(return_value={TOPIC_ID: _topic()})
    repo.interaction_states = AsyncMock(return_value={posts[0].id: {"like"}})
    profiles = MagicMock()
    profiles.get_many = AsyncMock(return_value={
        OTHER_ID: PublicUserProfile(OTHER_ID, "其他同学"),
        USER_ID: PublicUserProfile(USER_ID, "学生一号"),
    })
    service = PostQueryService(repo, profiles)
    result = asyncio.run(service.list(actor=_actor(), page=1, page_size=20))
    assert result.total == 2 and len(result.items) == 2
    assert result.items[0].author.display_name == "其他同学"
    assert result.items[0].interaction.liked is True
    assert result.items[0].moderation_case_id is None
    repo.topics_by_ids.assert_awaited_once()
    repo.interaction_states.assert_awaited_once()
    profiles.get_many.assert_awaited_once_with({OTHER_ID, USER_ID})


def test_anonymous_posts_never_resolve_or_expose_author() -> None:
    post = _post(anonymous=True, status="pending_review")
    repo = MagicMock()
    repo.get_visible = AsyncMock(return_value=post)
    repo.topics_by_ids = AsyncMock(return_value={TOPIC_ID: _topic()})
    repo.interaction_states = AsyncMock(return_value={})
    profiles = MagicMock(); profiles.get_many = AsyncMock()
    item = asyncio.run(PostQueryService(repo, profiles).get(actor=_actor(moderator=True), post_id=post.id))
    assert item.author.user_id is None
    assert item.author.display_name == "匿名同学" and item.author.is_anonymous is True
    assert str(OTHER_ID) not in str(item.author)
    profiles.get_many.assert_not_awaited()


def test_detail_hides_moderation_case_from_non_owner_but_shows_owner() -> None:
    post = _post(status="pending_review")
    repo = MagicMock(); repo.get_visible = AsyncMock(return_value=post)
    repo.topics_by_ids = AsyncMock(return_value={TOPIC_ID: _topic()})
    repo.interaction_states = AsyncMock(return_value={})
    profiles = MagicMock(); profiles.get_many = AsyncMock(return_value={OTHER_ID: PublicUserProfile(OTHER_ID, "其他")})
    moderator_item = asyncio.run(PostQueryService(repo, profiles).get(actor=_actor(moderator=True), post_id=post.id))
    assert moderator_item.moderation_case_id == post.moderation_case_id
    post.author_user_id = USER_ID
    profiles.get_many.return_value = {USER_ID: PublicUserProfile(USER_ID, "本人")}
    owner_item = asyncio.run(PostQueryService(repo, profiles).get(actor=_actor(), post_id=post.id))
    assert owner_item.moderation_case_id == post.moderation_case_id


def test_missing_or_inconsistent_post_is_safe_404_and_repositories_own_no_session() -> None:
    repo = MagicMock(); repo.get_visible = AsyncMock(return_value=None)
    service = PostQueryService(repo, MagicMock())
    with pytest.raises(PostNotFound):
        asyncio.run(service.get(actor=_actor(), post_id=uuid4()))
    source = __import__("inspect").getsource(PostRepository)
    assert ".commit(" not in source and ".rollback(" not in source and ".close(" not in source
