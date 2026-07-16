import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.community.comment_routes import get_comment_service
from app.modules.community.comments import (
    CommentData, CommentMutationResult, CommentPageData, CommentService,
)
from app.modules.community.errors import CommentParentInvalid, CommunityAnonymousNotAllowed
from app.modules.community.models import Comment, Post, Topic
from app.modules.community.posts import PublicAuthorData
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.idempotency import IdempotencyDecision
from app.modules.platform.moderation_scan import ScanResult

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
USER_ID = UUID("90000000-0000-4000-8000-000000000001")
POST_ID = UUID("75000000-0000-4000-8000-000000000001")
TOPIC_ID = UUID("74000000-0000-4000-8000-000000000001")
COMMENT_ID = UUID("76000000-0000-4000-8000-000000000001")


def _actor(permissions=("community:read", "community:write")):
    return AuthenticatedUser(
        user_id=USER_ID, username="student01", display_name="学生一号", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "学生"),), permissions=permissions,
        last_login_at=None, created_at=NOW, version=1,
    )


def _post():
    return Post(id=POST_ID, topic_id=TOPIC_ID, author_user_id=USER_ID, title="标题",
                content_markdown="正文", is_anonymous=False, status="published",
                risk_level="low", moderation_case_id=None,
                moderation_policy_version="v1", like_count=0, favorite_count=0,
                comment_count=0, report_count=0, published_at=NOW, version=1,
                created_at=NOW, updated_at=NOW, deleted_at=None)


def _topic(anonymous=True):
    return Topic(id=TOPIC_ID, code="tree-hole", name="树洞", description=None,
                 allow_anonymous=anonymous, sort_order=1, status="active",
                 created_by=USER_ID, version=1, created_at=NOW, updated_at=NOW,
                 deleted_at=None)


def _comment(status="published"):
    return Comment(id=COMMENT_ID, post_id=POST_ID, parent_comment_id=None,
                   author_user_id=USER_ID, content_markdown="评论", is_anonymous=False,
                   status=status, risk_level="low", moderation_case_id=None,
                   moderation_policy_version="v1",
                   published_at=NOW if status == "published" else None,
                   version=1, created_at=NOW, updated_at=NOW, deleted_at=None)


def _data():
    return CommentData(COMMENT_ID, POST_ID, None,
                       PublicAuthorData(USER_ID, "学生一号", None, False), "评论",
                       False, "published", None, NOW, 1, NOW, NOW)


def _service(*, topic=None, parent=True, scan_action="allow"):
    session = MagicMock()
    @asynccontextmanager
    async def begin(): yield
    session.begin.side_effect = begin; session.flush = AsyncMock()
    repo = MagicMock()
    repo.get_published_post = AsyncMock(return_value=_post())
    repo.get_topic = AsyncMock(return_value=topic or _topic())
    repo.get_published_parent = AsyncMock(return_value=_comment() if parent else None)
    repo.adjust_post_comment_count = AsyncMock(); repo.add = MagicMock()
    profiles = MagicMock(); profiles.get_many = AsyncMock(return_value={USER_ID: SimpleNamespace(display_name="学生一号", avatar_url=None)})
    moderation = MagicMock()
    risk = {"allow": "low", "mask": "medium", "review": "high", "block": "critical"}[scan_action]
    moderation.scan = AsyncMock(return_value=ScanResult(scan_action, risk, (), "v1", "已处理"))
    moderation.submit_case = AsyncMock(return_value=SimpleNamespace(id=uuid4()) if scan_action in {"review", "block"} else None)
    idem = MagicMock(); idem.begin = AsyncMock(return_value=IdempotencyDecision(uuid4())); idem.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    return CommentService(session=session, repository=repo, profiles=profiles,
                          moderation=moderation, idempotency=idem, audit=audit,
                          now=lambda: NOW), repo, moderation, audit


@pytest.mark.parametrize(("action", "status", "delta"), [
    ("allow", "published", 1), ("mask", "published", 1),
    ("review", "pending_review", None), ("block", "rejected", None),
])
def test_create_comment_scans_and_updates_count(action, status, delta) -> None:
    service, repo, moderation, audit = _service(scan_action=action)
    result = asyncio.run(service.create(
        actor=_actor(), post_id=POST_ID, parent_comment_id=None,
        content_markdown="评论", is_anonymous=False, idempotency_key="key",
        request_id="rid", request_body={"content_markdown": "评论"},
    ))
    item = repo.add.call_args.args[0]
    assert item.status == status and result.status_code == 201
    if delta is None: repo.adjust_post_comment_count.assert_not_awaited()
    else: repo.adjust_post_comment_count.assert_awaited_once_with(POST_ID, delta)
    assert "content_markdown" not in audit.record_success.call_args.kwargs["after_data"]
    assert moderation.scan.await_count == 1


def test_parent_and_anonymous_rules_fail_before_insert() -> None:
    service, repo, _, _ = _service(parent=False)
    with pytest.raises(CommentParentInvalid):
        asyncio.run(service.create(actor=_actor(), post_id=POST_ID,
                    parent_comment_id=uuid4(), content_markdown="评论", is_anonymous=False,
                    idempotency_key="key", request_id="rid", request_body={}))
    repo.add.assert_not_called()
    service, repo, _, _ = _service(topic=_topic(False))
    with pytest.raises(CommunityAnonymousNotAllowed):
        asyncio.run(service.create(actor=_actor(), post_id=POST_ID,
                    parent_comment_id=None, content_markdown="评论", is_anonymous=True,
                    idempotency_key="key", request_id="rid", request_body={}))
    repo.add.assert_not_called()


def test_list_batches_profiles_and_anonymous_hides_identity() -> None:
    service, repo, _, _ = _service()
    anonymous = _comment(); anonymous.id = uuid4(); anonymous.is_anonymous = True
    repo.list = AsyncMock(return_value=SimpleNamespace(items=(_comment(), anonymous), total=2))
    result = asyncio.run(service.list(post_id=POST_ID, page=1, page_size=20))
    assert result.total == 2 and result.items[1].author.user_id is None
    service._profiles.get_many.assert_awaited_once_with({USER_ID})


def _client(service, *, user=True, permissions=("community:read", "community:write")):
    app = create_app()
    async def service_override(): return service
    app.dependency_overrides[get_comment_service] = service_override
    if user:
        async def user_override(): return _actor(permissions)
        app.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(app)


def test_comment_routes_contract_auth_and_operation_ids() -> None:
    service = MagicMock()
    service.list = AsyncMock(return_value=CommentPageData((_data(),), 1, 20, 1))
    service.create = AsyncMock(return_value=CommentMutationResult(201, "original", {
        "code": "OK", "message": "success", "data": {}, "request_id": "original",
        "timestamp": NOW.isoformat(),
    }))
    service.update = AsyncMock(return_value=_data()); service.delete = AsyncMock()
    client = _client(service)
    assert client.get(f"/api/v1/posts/{POST_ID}/comments").status_code == 200
    assert client.post(f"/api/v1/posts/{POST_ID}/comments", json={"content_markdown": "评论"},
                       headers={"Idempotency-Key": "key"}).status_code == 201
    assert client.patch(f"/api/v1/comments/{COMMENT_ID}",
                        json={"content_markdown": "新评论", "version": 1}).status_code == 200
    assert client.delete(f"/api/v1/comments/{COMMENT_ID}").status_code == 204
    assert _client(service, user=False).get(f"/api/v1/posts/{POST_ID}/comments").status_code == 401
    assert _client(service, permissions=()).get(f"/api/v1/posts/{POST_ID}/comments").status_code == 403
    expected = {"listPostComments", "createComment", "updateComment", "deleteComment"}
    ids = [r.operation_id for r in create_app().routes if getattr(r, "operation_id", None) in expected]
    assert set(ids) == expected and len(ids) == 4
