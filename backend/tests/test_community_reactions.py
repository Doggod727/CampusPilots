import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.main import create_app
from app.modules.community.errors import CommunityContentPendingReview, PostNotFound
from app.modules.community.models import Post
from app.modules.community.reaction_routes import get_reaction_service
from app.modules.community.reactions import ReactionData, ReactionService
from app.modules.community.repositories import ReactionRepository
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
USER_ID = UUID("90000000-0000-4000-8000-000000000001")
POST_ID = UUID("75000000-0000-4000-8000-000000000001")


def _actor(permissions=("community:write",)):
    return AuthenticatedUser(user_id=USER_ID, username="student01", display_name="学生",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "学生"),), permissions=permissions,
        last_login_at=None, created_at=NOW, version=1)


def _post(status="published"):
    return Post(id=POST_ID, topic_id=uuid4(), author_user_id=USER_ID, title="标题",
        content_markdown="正文", is_anonymous=False, status=status, risk_level="low",
        moderation_case_id=None, moderation_policy_version="v1", like_count=2,
        favorite_count=3, comment_count=0, report_count=0,
        published_at=NOW if status == "published" else None, version=1,
        created_at=NOW, updated_at=NOW, deleted_at=None)


def _service(post=None, *, changed=True):
    session = MagicMock()
    @asynccontextmanager
    async def begin(): yield
    session.begin.side_effect = begin
    repo = MagicMock(); repo.get_post_for_update = AsyncMock(return_value=post)
    repo.insert = AsyncMock(return_value=changed); repo.delete = AsyncMock(return_value=changed)
    repo.adjust_count = AsyncMock(return_value=(3, 3))
    return ReactionService(session=session, repository=repo), repo


def test_put_and_delete_only_adjust_when_fact_changes() -> None:
    service, repo = _service(_post(), changed=True)
    result = asyncio.run(service.put(actor=_actor(), post_id=POST_ID, reaction_type="like"))
    assert result.active and result.like_count == 3
    repo.adjust_count.assert_awaited_once_with(post_id=POST_ID, reaction_type="like", delta=1)
    service, repo = _service(_post(), changed=False)
    result = asyncio.run(service.delete(actor=_actor(), post_id=POST_ID, reaction_type="favorite"))
    assert not result.active and result.favorite_count == 3
    repo.adjust_count.assert_not_awaited()


def test_pending_owner_gets_conflict_but_nonpublic_delete_is_hidden() -> None:
    service, repo = _service(_post("pending_review"))
    with pytest.raises(CommunityContentPendingReview):
        asyncio.run(service.put(actor=_actor(), post_id=POST_ID, reaction_type="like"))
    with pytest.raises(PostNotFound):
        asyncio.run(service.delete(actor=_actor(), post_id=POST_ID, reaction_type="like"))
    repo.insert.assert_not_awaited(); repo.delete.assert_not_awaited()


def test_repository_uses_conflict_do_nothing_and_returning() -> None:
    session = MagicMock(); result = MagicMock(); result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    asyncio.run(ReactionRepository(session).insert(post_id=POST_ID, user_id=USER_ID,
                                                   reaction_type="like"))
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT DO NOTHING" in sql and "RETURNING" in sql


def _client(service, *, user=True, permissions=("community:write",)):
    app = create_app()
    async def service_override(): return service
    app.dependency_overrides[get_reaction_service] = service_override
    if user:
        async def user_override(): return _actor(permissions)
        app.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(app)


def test_reaction_routes_contract_permissions_and_ids() -> None:
    service = MagicMock()
    service.put = AsyncMock(return_value=ReactionData(POST_ID, "like", True, 1, 0))
    service.delete = AsyncMock(return_value=ReactionData(POST_ID, "like", False, 0, 0))
    client = _client(service)
    assert client.put(f"/api/v1/posts/{POST_ID}/reactions/like").json()["data"]["active"] is True
    assert client.delete(f"/api/v1/posts/{POST_ID}/reactions/like").json()["data"]["active"] is False
    assert client.put(f"/api/v1/posts/{POST_ID}/reactions/bad").status_code == 422
    assert _client(service, user=False).put(f"/api/v1/posts/{POST_ID}/reactions/like").status_code == 401
    assert _client(service, permissions=()).put(f"/api/v1/posts/{POST_ID}/reactions/like").status_code == 403
    expected = {"putPostReaction", "deletePostReaction"}
    ids = [r.operation_id for r in create_app().routes if getattr(r, "operation_id", None) in expected]
    assert set(ids) == expected and len(ids) == 2
