from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.community.topic_routes import get_topic_service
from app.modules.community.topics import TopicData, TopicMutationResult, TopicPageData
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

TOPIC_ID = UUID("74000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def _user(permissions=("community:read", "community:moderate")) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=UUID("90000000-0000-4000-8000-000000000004"),
        username="community01", display_name="社区运营员", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "community_operator", "社区运营员"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def _topic(status="active", version=1) -> TopicData:
    return TopicData(
        id=TOPIC_ID, code="campus-life", name="校园生活", description="校园交流",
        allow_anonymous=False, sort_order=10, status=status, version=version,
        created_at=NOW, updated_at=NOW,
    )


def _client(service, *, user=True, permissions=("community:read", "community:moderate")):
    app = create_app()
    async def service_override(): return service
    app.dependency_overrides[get_topic_service] = service_override
    if user:
        async def user_override(): return _user(permissions)
        app.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(app)


def test_list_topics_returns_strict_page_and_forwards_filters() -> None:
    service = MagicMock()
    service.list = AsyncMock(return_value=TopicPageData((_topic(),), 2, 10, 11))
    response = _client(service).get(
        "/api/v1/topics?page=2&page_size=10&status=active",
        headers={"X-Request-Id": "topic-list-1"},
    )
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "topic-list-1"
    assert response.json()["data"]["pagination"] == {
        "page": 2, "page_size": 10, "total": 11, "total_pages": 2
    }
    assert set(response.json()["data"]["items"][0]) == {
        "id", "code", "name", "description", "allow_anonymous", "sort_order",
        "status", "version", "created_at", "updated_at",
    }
    service.list.assert_awaited_once_with(page=2, page_size=10, status="active")


def test_create_topic_requires_idempotency_and_returns_original_envelope() -> None:
    body = {
        "code": "OK", "message": "success",
        "data": {
            "id": str(TOPIC_ID), "code": "new-topic", "name": "新话题",
            "description": None, "allow_anonymous": False, "sort_order": 0,
            "status": "active", "version": 1,
            "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
        },
        "request_id": "original-request", "timestamp": NOW.isoformat(),
    }
    service = MagicMock()
    service.create = AsyncMock(return_value=TopicMutationResult(201, "original-request", body))
    response = _client(service).post(
        "/api/v1/topics", json={"code": "new-topic", "name": "新话题"},
        headers={"Idempotency-Key": "topic-key"},
    )
    assert response.status_code == 201 and response.json() == body
    assert service.create.await_args.kwargs["idempotency_key"] == "topic-key"
    assert _client(service).post(
        "/api/v1/topics", json={"code": "new-topic", "name": "新话题"}
    ).status_code == 422


def test_get_update_and_delete_topics_use_contract_shapes() -> None:
    service = MagicMock()
    service.get = AsyncMock(return_value=_topic())
    service.update = AsyncMock(return_value=_topic(status="archived", version=2))
    service.delete = AsyncMock()
    client = _client(service)
    assert client.get(f"/api/v1/topics/{TOPIC_ID}").status_code == 200
    updated = client.patch(
        f"/api/v1/topics/{TOPIC_ID}", json={"status": "archived", "version": 1}
    )
    assert updated.status_code == 200 and updated.json()["data"]["version"] == 2
    assert service.update.await_args.kwargs["changes"] == {"status": "archived"}
    deleted = client.delete(f"/api/v1/topics/{TOPIC_ID}")
    assert deleted.status_code == 204 and deleted.content == b""


def test_topic_routes_enforce_auth_permissions_and_validation() -> None:
    service = MagicMock(); service.list = AsyncMock(return_value=TopicPageData((), 1, 20, 0))
    assert _client(service, user=False).get("/api/v1/topics").status_code == 401
    assert _client(service, permissions=()).get("/api/v1/topics").status_code == 403
    assert _client(service, permissions=("community:read",)).post(
        "/api/v1/topics", json={"code": "new-topic", "name": "新话题"},
        headers={"Idempotency-Key": "topic-key"},
    ).status_code == 403
    client = _client(service)
    assert client.get("/api/v1/topics?page=0").status_code == 422
    assert client.patch(f"/api/v1/topics/{TOPIC_ID}", json={"name": None, "version": 1}).status_code == 422
    assert client.get("/api/v1/topics/not-a-uuid").status_code == 422


def test_all_five_topic_operation_ids_are_registered_once() -> None:
    expected = {"listTopics", "createTopic", "getTopic", "updateTopic", "deleteTopic"}
    implemented = [
        route.operation_id for route in create_app().routes
        if getattr(route, "operation_id", None) in expected
    ]
    assert set(implemented) == expected and len(implemented) == 5
