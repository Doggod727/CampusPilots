import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.community.moderation_handler import (
    CommunityModerationHandler, ModerationTargetConflict, register_community_handlers,
)
from app.modules.community.models import CampusEvent, Comment, LostFoundItem, Post
from app.modules.platform.moderation_decision import default_moderation_handler_registry
from app.modules.platform.moderation_handlers import ModerationHandlerRegistry

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
CASE_ID = UUID("61000000-0000-4000-8000-000000000001")
TARGET_ID = UUID("75000000-0000-4000-8000-000000000001")
POST_ID = UUID("75000000-0000-4000-8000-000000000002")


def _comment(status="pending_review", *, deleted=False):
    return Comment(id=TARGET_ID, post_id=POST_ID, parent_comment_id=None,
                   author_user_id=uuid4(), content_markdown="评论", is_anonymous=False,
                   status="deleted" if deleted else status, risk_level="high",
                   moderation_case_id=CASE_ID, moderation_policy_version="v1",
                   published_at=NOW if status == "published" else None, version=1,
                   created_at=NOW, updated_at=NOW, deleted_at=NOW if deleted else None)


def _session(item):
    session = MagicMock(); result = MagicMock(); result.scalar_one_or_none.return_value = item
    session.execute = AsyncMock(return_value=result); session.flush = AsyncMock()
    return session


def test_default_registry_registers_all_four_community_targets() -> None:
    registry = default_moderation_handler_registry()
    for target in ("post", "comment", "event", "lost_found"):
        assert registry.resolve(target_module="community", target_type=target) is not None


def test_comment_approve_publishes_and_increments_once() -> None:
    item = _comment(); session = _session(item)
    handler = CommunityModerationHandler(Comment)
    asyncio.run(handler.approve(session=session, case_id=CASE_ID, target_id=TARGET_ID,
                                reason="通过", actor=MagicMock()))
    assert item.status == "published" and item.published_at is not None
    assert session.execute.await_count == 2 and session.flush.await_count == 1
    # A direct duplicate sees published and does not emit another counter update.
    session.execute.reset_mock(); result = MagicMock(); result.scalar_one_or_none.return_value = item
    session.execute.return_value = result
    asyncio.run(handler.approve(session=session, case_id=CASE_ID, target_id=TARGET_ID,
                                reason="重复", actor=MagicMock()))
    assert session.execute.await_count == 1


def test_published_comment_rejection_decrements_once() -> None:
    item = _comment("published"); session = _session(item)
    asyncio.run(CommunityModerationHandler(Comment).reject(
        session=session, case_id=CASE_ID, target_id=TARGET_ID,
        reason="拒绝", actor=MagicMock(),
    ))
    assert item.status == "rejected" and item.published_at is None
    assert session.execute.await_count == 2


def test_escalation_and_deleted_target_never_restore_or_count() -> None:
    item = _comment(); session = _session(item)
    handler = CommunityModerationHandler(Comment)
    asyncio.run(handler.escalate(session=session, case_id=CASE_ID, target_id=TARGET_ID,
                                 reason="升级", actor=MagicMock()))
    assert item.status == "pending_review" and session.execute.await_count == 1
    deleted = _comment(deleted=True); session = _session(deleted)
    asyncio.run(handler.approve(session=session, case_id=CASE_ID, target_id=TARGET_ID,
                                reason="通过", actor=MagicMock()))
    assert deleted.status == "deleted" and session.execute.await_count == 1


def test_missing_or_mismatched_target_is_safe_conflict() -> None:
    for item in (None, _comment()):
        if item is not None: item.moderation_case_id = uuid4()
        with pytest.raises(ModerationTargetConflict):
            asyncio.run(CommunityModerationHandler(Comment).approve(
                session=_session(item), case_id=CASE_ID, target_id=TARGET_ID,
                reason="通过", actor=MagicMock(),
            ))
