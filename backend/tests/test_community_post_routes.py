from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.community.post_routes import get_post_query_service, get_post_service
from app.modules.community.posts import (
    PostData,
    PostInteractionData,
    PostMutationResult,
    PostPageData,
    PublicAuthorData,
)
from app.modules.community.topics import TopicData
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
USER_ID = UUID("90000000-0000-4000-8000-000000000001")
TOPIC_ID = UUID("74000000-0000-4000-8000-000000000001")
POST_ID = UUID("75000000-0000-4000-8000-000000000001")


def _user(permissions=("community:read", "community:write")):
    return AuthenticatedUser(
        user_id=USER_ID, username="student01", display_name="学生一号", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "学生"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def _post():
    topic = TopicData(
        TOPIC_ID, "campus-life", "校园生活", None, False, 10, "active", 1, NOW, NOW,
    )
    return PostData(
        POST_ID, topic, PublicAuthorData(USER_ID, "学生一号", None, False),
        "帖子标题", "帖子正文", False, "published", None, 0, 0, 0, 0,
        PostInteractionData(False, False), NOW, 1, NOW, NOW,
    )


def _client(query_service, write_service, *, user=True, permissions=("community:read", "community:write")):
    app = create_app()
    async def query_override(): return query_service
    async def write_override(): return write_service
    app.dependency_overrides[get_post_query_service] = query_override
    app.dependency_overrides[get_post_service] = write_override
    if user:
        async def user_override(): return _user(permissions)
        app.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(app)


def test_list_and_get_posts_return_strict_contract_and_request_id() -> None:
    query = MagicMock()
    query.list = AsyncMock(return_value=PostPageData((_post(),), 1, 20, 1))
    query.get = AsyncMock(return_value=_post())
    client = _client(query, MagicMock())
    response = client.get("/api/v1/posts", headers={"X-Request-Id": "posts-rid"})
    assert response.status_code == 200 and response.headers["X-Request-Id"] == "posts-rid"
    assert response.json()["data"]["pagination"]["total_pages"] == 1
    assert set(response.json()["data"]["items"][0]) == {
        "id", "topic", "author", "title", "content_markdown", "is_anonymous",
        "status", "moderation_case_id", "like_count", "favorite_count",
        "comment_count", "report_count", "interaction", "published_at", "version",
        "created_at", "updated_at",
    }
    assert client.get(f"/api/v1/posts/{POST_ID}").status_code == 200


def test_create_returns_original_envelope_and_requires_idempotency() -> None:
    body = {
        "code": "OK", "message": "success", "data": {},
        "request_id": "original", "timestamp": NOW.isoformat(),
    }
    service = MagicMock()
    service.create = AsyncMock(return_value=PostMutationResult(201, "original", body))
    client = _client(MagicMock(), service)
    response = client.post(
        "/api/v1/posts",
        json={"topic_id": str(TOPIC_ID), "title": "标题", "content_markdown": "正文"},
        headers={"Idempotency-Key": "post-key"},
    )
    assert response.status_code == 201 and response.json() == body
    assert service.create.await_args.kwargs["topic_id"] == TOPIC_ID
    missing = client.post(
        "/api/v1/posts",
        json={"topic_id": str(TOPIC_ID), "title": "标题", "content_markdown": "正文"},
    )
    assert missing.status_code == 422


def test_update_and_delete_forward_only_contract_changes() -> None:
    service = MagicMock()
    service.update = AsyncMock(return_value=_post())
    service.delete = AsyncMock()
    client = _client(MagicMock(), service)
    updated = client.patch(
        f"/api/v1/posts/{POST_ID}", json={"title": "新标题", "version": 1},
    )
    assert updated.status_code == 200
    assert service.update.await_args.kwargs["changes"] == {"title": "新标题"}
    deleted = client.delete(f"/api/v1/posts/{POST_ID}")
    assert deleted.status_code == 204 and deleted.content == b""


def test_post_routes_enforce_auth_permissions_and_validation() -> None:
    query = MagicMock(); query.list = AsyncMock(return_value=PostPageData((), 1, 20, 0))
    assert _client(query, MagicMock(), user=False).get("/api/v1/posts").status_code == 401
    assert _client(query, MagicMock(), permissions=()).get("/api/v1/posts").status_code == 403
    client = _client(query, MagicMock())
    assert client.get("/api/v1/posts?page=0").status_code == 422
    assert client.get("/api/v1/posts?sort=invalid").status_code == 422
    assert client.get("/api/v1/posts/not-a-uuid").status_code == 422
    assert client.patch(
        f"/api/v1/posts/{POST_ID}", json={"title": None, "version": 1}
    ).status_code == 422


def test_all_ten_second_batch_operation_ids_are_registered_once() -> None:
    expected = {
        "listTopics", "createTopic", "getTopic", "updateTopic", "deleteTopic",
        "listPosts", "createPost", "getPost", "updatePost", "deletePost",
    }
    operation_ids = [
        route.operation_id for route in create_app().routes
        if getattr(route, "operation_id", None) in expected
    ]
    assert set(operation_ids) == expected and len(operation_ids) == 10
    all_ids = [
        route.operation_id for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    assert len(all_ids) == len(set(all_ids))
