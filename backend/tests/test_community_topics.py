import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.community.errors import (
    CommunityResourceVersionConflict,
    TopicCodeConflict,
    TopicHasPosts,
    TopicNameConflict,
    TopicNotFound,
)
from app.modules.community.models import Topic
from app.modules.community.repositories import TopicPage, TopicRepository
from app.modules.community.topics import TopicService
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision, IdempotencyReplay

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
USER_ID = UUID("90000000-0000-4000-8000-000000000004")


class _Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=USER_ID, username="community01", display_name="社区运营员",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "community_operator", "社区运营员"),),
        permissions=("community:read", "community:moderate"), last_login_at=None,
        created_at=NOW, version=1,
    )


def _topic(**values) -> Topic:
    defaults = dict(
        id=uuid4(), code="campus-life", name="校园生活", description="desc",
        allow_anonymous=False, sort_order=10, status="active", created_by=USER_ID,
        version=1, created_at=NOW, updated_at=NOW, deleted_at=None,
    )
    defaults.update(values)
    return Topic(**defaults)


def _service(item: Topic | None = None, decision=None):
    session = MagicMock(); session.begin = MagicMock(return_value=_Transaction())
    session.flush = AsyncMock()
    repo = MagicMock()
    repo.list = AsyncMock(return_value=TopicPage((_topic(),), 1))
    repo.get = AsyncMock(return_value=item)
    repo.get_for_update = AsyncMock(return_value=item)
    repo.code_exists = AsyncMock(return_value=False)
    repo.name_exists = AsyncMock(return_value=False)
    repo.has_non_deleted_posts = AsyncMock(return_value=False)
    idem = MagicMock()
    idem.begin = AsyncMock(return_value=decision or IdempotencyDecision(record_id=uuid4()))
    idem.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    return TopicService(
        session=session, repository=repo, idempotency=idem, audit=audit, now=lambda: NOW
    ), session, repo, idem, audit


def test_repository_list_filters_deleted_status_and_uses_stable_sort() -> None:
    session = MagicMock()
    rows = MagicMock(); rows.scalars.return_value.all.return_value = []
    count = MagicMock(); count.scalar_one.return_value = 0
    session.execute = AsyncMock(side_effect=[rows, count])
    result = asyncio.run(TopicRepository(session).list(page=2, page_size=20, status="active"))
    assert result == TopicPage((), 0)
    statements = [str(call.args[0].compile(dialect=postgresql.dialect())) for call in session.execute.await_args_list]
    assert "topics.deleted_at IS NULL" in statements[0]
    assert "topics.status =" in statements[0]
    assert "ORDER BY community.topics.sort_order, community.topics.name, community.topics.id" in statements[0]
    assert "LIMIT" in statements[0] and "OFFSET" in statements[0]


def test_repository_never_controls_session_lifecycle() -> None:
    source = __import__("inspect").getsource(TopicRepository)
    assert ".commit(" not in source and ".rollback(" not in source and ".close(" not in source


def test_topic_service_lists_and_hides_missing_topics() -> None:
    service, _, _, _, _ = _service(None)
    page = asyncio.run(service.list(page=1, page_size=20))
    assert page.total == 1 and page.items[0].code == "campus-life"
    with pytest.raises(TopicNotFound):
        asyncio.run(service.get(uuid4()))


def test_create_is_idempotent_audited_and_does_not_record_description() -> None:
    service, session, repo, idem, audit = _service()
    result = asyncio.run(service.create(
        actor=_actor(), code="new-topic", name="新话题", description="private description",
        allow_anonymous=False, sort_order=5, idempotency_key="topic-key",
        request_id="topic-request", request_body={"code": "new-topic"},
    ))
    assert result.status_code == 201 and result.body["data"]["code"] == "new-topic"
    repo.add.assert_called_once()
    session.flush.assert_awaited_once()
    idem.complete.assert_awaited_once()
    audit_payload = audit.record_success.call_args.kwargs
    assert "private description" not in str(audit_payload)

    replay = IdempotencyReplay(201, result.body, "topic", str(repo.add.call_args.args[0].id))
    replay_service, _, replay_repo, _, _ = _service(
        decision=IdempotencyDecision(record_id=uuid4(), replay=replay)
    )
    replayed = asyncio.run(replay_service.create(
        actor=_actor(), code="new-topic", name="新话题", description=None,
        allow_anonymous=False, sort_order=5, idempotency_key="topic-key",
        request_id="different", request_body={"code": "new-topic"},
    ))
    assert replayed.body == result.body
    replay_repo.add.assert_not_called()


@pytest.mark.parametrize("field,error", [("code", TopicCodeConflict), ("name", TopicNameConflict)])
def test_create_maps_code_and_name_conflicts(field, error) -> None:
    service, _, repo, _, _ = _service()
    getattr(repo, f"{field}_exists").return_value = True
    with pytest.raises(error):
        asyncio.run(service.create(
            actor=_actor(), code="new-topic", name="新话题", description=None,
            allow_anonymous=False, sort_order=0, idempotency_key="key",
            request_id="request", request_body={},
        ))


def test_update_checks_version_and_audits_only_safe_state() -> None:
    item = _topic(version=2)
    service, _, _, _, audit = _service(item)
    with pytest.raises(CommunityResourceVersionConflict):
        asyncio.run(service.update(
            actor=_actor(), topic_id=item.id, version=1,
            changes={"description": "private"}, request_id="request",
        ))
    updated = asyncio.run(service.update(
        actor=_actor(), topic_id=item.id, version=2,
        changes={"description": "private", "status": "archived"}, request_id="request",
    ))
    assert updated.version == 3 and updated.status == "archived"
    assert "private" not in str(audit.record_success.call_args.kwargs)


def test_delete_rejects_topics_with_non_deleted_posts() -> None:
    item = _topic()
    service, _, repo, _, _ = _service(item)
    repo.has_non_deleted_posts.return_value = True
    with pytest.raises(TopicHasPosts):
        asyncio.run(service.delete(actor=_actor(), topic_id=item.id, request_id="request"))
    assert item.deleted_at is None
    repo.has_non_deleted_posts.return_value = False
    asyncio.run(service.delete(actor=_actor(), topic_id=item.id, request_id="request"))
    assert item.deleted_at == NOW and item.version == 2
