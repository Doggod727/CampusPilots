import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.community.models import ContentReport, Post
from app.modules.community.report_routes import get_report_service
from app.modules.community.reports import ReportMutationResult, ReportService
from app.modules.community.repositories import ReportTarget
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.idempotency import IdempotencyDecision

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
USER_ID = UUID("90000000-0000-4000-8000-000000000001")
TARGET_ID = UUID("75000000-0000-4000-8000-000000000001")


def _actor(permissions=("community:write",)):
    return AuthenticatedUser(user_id=USER_ID, username="student01", display_name="学生",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "学生"),), permissions=permissions,
        last_login_at=None, created_at=NOW, version=1)


def _post():
    return Post(id=TARGET_ID, topic_id=uuid4(), author_user_id=USER_ID, title="标题",
        content_markdown="正文", is_anonymous=False, status="published", risk_level="low",
        moderation_case_id=None, moderation_policy_version="v1", like_count=0,
        favorite_count=0, comment_count=0, report_count=0, published_at=NOW,
        version=1, created_at=NOW, updated_at=NOW, deleted_at=None)


def _report(case_id=None):
    return ContentReport(id=uuid4(), reporter_user_id=USER_ID, target_type="post",
        target_id=TARGET_ID, reason_code="spam", details="举报详情", status="linked",
        moderation_case_id=case_id or uuid4(), created_at=NOW, updated_at=NOW)


def _service(*, existing=None, pending_case=None):
    session = MagicMock()
    @asynccontextmanager
    async def begin(): yield
    session.begin.side_effect = begin; session.flush = AsyncMock()
    repo = MagicMock(); repo.get_target_for_update = AsyncMock(return_value=ReportTarget(_post(), "post"))
    repo.get_existing = AsyncMock(return_value=existing); repo.get_pending_case = AsyncMock(return_value=pending_case)
    repo.add = MagicMock(); repo.increment_post_report_count = AsyncMock()
    moderation = MagicMock(); moderation.submit_case = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    idem = MagicMock(); idem.begin = AsyncMock(return_value=IdempotencyDecision(uuid4())); idem.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    return ReportService(session=session, repository=repo, moderation=moderation,
                         idempotency=idem, audit=audit, now=lambda: NOW), repo, moderation, audit


def test_first_report_creates_case_fact_and_post_count_without_audit_details() -> None:
    service, repo, moderation, audit = _service()
    result = asyncio.run(service.submit(actor=_actor(), target_type="post", target_id=TARGET_ID,
        reason_code="spam", details="举报详情", idempotency_key="key", request_id="rid",
        request_body={"target_type": "post"}))
    assert result.status_code == 201 and repo.add.call_count == 1
    repo.increment_post_report_count.assert_awaited_once_with(TARGET_ID)
    scan = moderation.submit_case.await_args.kwargs["result"]
    assert scan.action == "review" and scan.risk_level == "high" and scan.hits == ()
    assert "details" not in audit.record_success.call_args.kwargs["after_data"]


def test_existing_business_report_returns_first_without_case_or_count() -> None:
    existing = _report(); service, repo, moderation, _ = _service(existing=existing)
    result = asyncio.run(service.submit(actor=_actor(), target_type="post", target_id=TARGET_ID,
        reason_code="other", details="不同详情", idempotency_key="new", request_id="rid",
        request_body={"target_type": "post"}))
    assert result.body["data"]["id"] == str(existing.id)
    moderation.submit_case.assert_not_awaited(); repo.add.assert_not_called()
    repo.increment_post_report_count.assert_not_awaited()


def test_pending_case_is_reused_for_new_report() -> None:
    case = SimpleNamespace(id=uuid4()); service, repo, moderation, _ = _service(pending_case=case)
    result = asyncio.run(service.submit(actor=_actor(), target_type="post", target_id=TARGET_ID,
        reason_code="unsafe", details="危险内容", idempotency_key="key", request_id="rid",
        request_body={}))
    assert result.body["data"]["moderation_case_id"] == str(case.id)
    moderation.submit_case.assert_not_awaited()


def _client(service, *, user=True, permissions=("community:write",)):
    app = create_app()
    async def service_override(): return service
    app.dependency_overrides[get_report_service] = service_override
    if user:
        async def user_override(): return _actor(permissions)
        app.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(app)


def test_report_route_is_strict_secured_and_registered() -> None:
    body = {"code": "OK", "message": "success", "data": {
        "id": str(uuid4()), "target_type": "post", "target_id": str(TARGET_ID),
        "reason_code": "spam", "status": "linked", "moderation_case_id": str(uuid4()),
        "created_at": NOW.isoformat()}, "request_id": "rid", "timestamp": NOW.isoformat()}
    service = MagicMock(); service.submit = AsyncMock(return_value=ReportMutationResult(201, "rid", body))
    payload = {"target_type": "post", "target_id": str(TARGET_ID),
               "reason_code": "spam", "details": "举报详情"}
    assert _client(service).post("/api/v1/reports", json=payload,
        headers={"Idempotency-Key": "key"}).status_code == 201
    assert _client(service).post("/api/v1/reports", json={**payload, "extra": 1},
        headers={"Idempotency-Key": "key"}).status_code == 422
    assert _client(service, user=False).post("/api/v1/reports", json=payload,
        headers={"Idempotency-Key": "key"}).status_code == 401
    assert _client(service, permissions=()).post("/api/v1/reports", json=payload,
        headers={"Idempotency-Key": "key"}).status_code == 403
    ids = [r.operation_id for r in create_app().routes if getattr(r, "operation_id", None) == "createContentReport"]
    assert ids == ["createContentReport"]
