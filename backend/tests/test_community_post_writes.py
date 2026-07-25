import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.community.errors import (
    CommunityAnonymousNotAllowed,
    CommunityResourceVersionConflict,
)
from app.modules.community.models import Post, Topic
from app.modules.community.posts import PostService
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser, PermissionDenied
from app.modules.platform.idempotency import IdempotencyDecision, IdempotencyReplay
from app.modules.platform.moderation_scan import ScanResult

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
USER_ID = UUID("90000000-0000-4000-8000-000000000001")
OTHER_ID = UUID("90000000-0000-4000-8000-000000000002")
TOPIC_ID = UUID("74000000-0000-4000-8000-000000000001")


def _actor(*, moderator=False):
    permissions = ("community:write", "community:moderate") if moderator else ("community:write",)
    return AuthenticatedUser(
        user_id=USER_ID, username="student01", display_name="学生一号", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "学生"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def _topic(*, anonymous=True):
    return Topic(
        id=TOPIC_ID, code="tree-hole", name="树洞", description=None,
        allow_anonymous=anonymous, sort_order=30, status="active",
        created_by=OTHER_ID, version=1, created_at=NOW, updated_at=NOW,
        deleted_at=None,
    )


def _post(*, owner=USER_ID, version=1):
    return Post(
        id=uuid4(), topic_id=TOPIC_ID, author_user_id=owner, title="原始标题",
        content_markdown="原始正文", is_anonymous=False, status="published",
        risk_level="low", moderation_case_id=None,
        moderation_policy_version="m4-sensitive-v1", like_count=0,
        favorite_count=0, comment_count=0, report_count=0, published_at=NOW,
        version=version, created_at=NOW, updated_at=NOW, deleted_at=None,
    )


def _scan(action, *, sanitized="文本"):
    risk = {"allow": "low", "mask": "medium", "review": "high", "block": "critical"}[action]
    return ScanResult(action, risk, (), "m4-sensitive-v1", sanitized)


def _service(*, topic=None, item=None, scans=None, decision=None):
    session = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock()
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    session.flush = AsyncMock()
    repository = MagicMock()
    repository.get_active_topic = AsyncMock(return_value=topic or _topic())
    repository.get_for_update = AsyncMock(return_value=item)
    repository.add = MagicMock()
    queries = MagicMock()
    queries.hydrate = AsyncMock(side_effect=lambda **kwargs: SimpleNamespace(id=kwargs["item"].id))
    moderation = MagicMock()
    moderation.scan = AsyncMock(side_effect=scans or [_scan("allow", sanitized="标题"), _scan("allow", sanitized="正文")])
    moderation.submit_case = AsyncMock(return_value=None)
    idempotency = MagicMock()
    idempotency.begin = AsyncMock(return_value=decision or IdempotencyDecision(uuid4()))
    idempotency.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    service = PostService(
        session=session, repository=repository, queries=queries,
        moderation=moderation, idempotency=idempotency, audit=audit,
        now=lambda: NOW,
    )
    return service, session, repository, queries, moderation, idempotency, audit


@pytest.mark.parametrize(
    ("action", "status", "case_expected"),
    [("allow", "published", False), ("mask", "published", False),
     ("review", "pending_review", True), ("block", "rejected", True)],
)
def test_create_scans_both_fields_and_applies_highest_action(action, status, case_expected) -> None:
    scans = [_scan("mask", sanitized="已遮罩标题"), _scan(action, sanitized="已处理正文")]
    service, _, repository, queries, moderation, _, audit = _service(scans=scans)
    if case_expected:
        moderation.submit_case.return_value = SimpleNamespace(id=uuid4())
    # Isolate mutation behavior from response serialization.
    from app.modules.community import posts as module
    original = module.post_response_body
    module.post_response_body = lambda item, **kwargs: {
        "request_id": kwargs["request_id"], "timestamp": NOW.isoformat(), "data": {}
    }
    try:
        result = asyncio.run(service.create(
            actor=_actor(), topic_id=TOPIC_ID, title="标题", content_markdown="正文",
            is_anonymous=False, idempotency_key="key", request_id="rid",
            request_body={"title": "标题"},
        ))
    finally:
        module.post_response_body = original
    item = repository.add.call_args.args[0]
    expected_status = "published" if action in {"allow", "mask"} else status
    assert item.status == expected_status
    assert item.title == "已遮罩标题" and item.content_markdown == "已处理正文"
    assert (item.published_at is not None) is (expected_status == "published")
    assert (item.moderation_case_id is not None) is case_expected
    assert result.status_code == 201 and queries.hydrate.await_count == 1
    assert "title" not in audit.record_success.call_args.kwargs["after_data"]
    assert "content_markdown" not in audit.record_success.call_args.kwargs["after_data"]


def test_anonymous_create_requires_anonymous_topic_and_writes_nothing() -> None:
    service, session, repository, _, moderation, _, _ = _service(topic=_topic(anonymous=False))
    with pytest.raises(CommunityAnonymousNotAllowed):
        asyncio.run(service.create(
            actor=_actor(), topic_id=TOPIC_ID, title="标题", content_markdown="正文",
            is_anonymous=True, idempotency_key="key", request_id="rid", request_body={},
        ))
    repository.add.assert_not_called()
    moderation.scan.assert_not_awaited()
    session.flush.assert_not_awaited()


def test_create_replays_original_idempotent_response_without_scanning() -> None:
    body = {"request_id": "original", "timestamp": NOW.isoformat(), "data": {}}
    replay = IdempotencyDecision(
        uuid4(), replay=IdempotencyReplay(201, body, "post", str(uuid4()))
    )
    service, _, repository, _, moderation, _, _ = _service(decision=replay)
    result = asyncio.run(service.create(
        actor=_actor(), topic_id=TOPIC_ID, title="标题", content_markdown="正文",
        is_anonymous=False, idempotency_key="key", request_id="new", request_body={},
    ))
    assert result.body == body and result.request_id == "original"
    repository.get_active_topic.assert_not_awaited()
    moderation.scan.assert_not_awaited()


def test_update_enforces_owner_and_version_before_rescan() -> None:
    foreign = _post(owner=OTHER_ID)
    service, _, _, _, moderation, _, _ = _service(item=foreign)
    with pytest.raises(PermissionDenied):
        asyncio.run(service.update(
            actor=_actor(), post_id=foreign.id, version=1,
            changes={"title": "新标题"}, request_id="rid",
        ))
    moderation.scan.assert_not_awaited()

    owned = _post(version=2)
    service, _, _, _, moderation, _, _ = _service(item=owned)
    with pytest.raises(CommunityResourceVersionConflict):
        asyncio.run(service.update(
            actor=_actor(), post_id=owned.id, version=1,
            changes={"title": "新标题"}, request_id="rid",
        ))
    moderation.scan.assert_not_awaited()


def test_delete_is_logical_and_moderator_can_delete_foreign_post() -> None:
    item = _post(owner=OTHER_ID)
    service, session, _, _, _, _, audit = _service(item=item)
    asyncio.run(service.delete(actor=_actor(moderator=True), post_id=item.id, request_id="rid"))
    assert item.status == "deleted" and item.deleted_at == NOW and item.version == 2
    session.flush.assert_awaited_once()
    assert audit.record_success.call_args.kwargs["after_data"] == {"deleted": True, "version": 2}
