import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.modules.campus_service.service_progress import (
    CampusSystemInvalidResponse,
    CampusSystemTimedOut,
    CampusSystemUnavailable,
    CampusSystemUnsupported,
    MockCampusSystemAdapter,
    ServiceProgressNotFound,
    ServiceProgressService,
)

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
OTHER_USER_ID = UUID("90000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 7, 16, 8, 30, tzinfo=UTC)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _service(adapter=None, **kwargs) -> ServiceProgressService:
    return ServiceProgressService(
        adapter=adapter if adapter is not None else MockCampusSystemAdapter(USER_ID, now=lambda: NOW),
        **kwargs,
    )


def _query(service: ServiceProgressService, **overrides):
    arguments = {
        "actor_user_id": USER_ID,
        "actor_username": "student01",
        "system_code": "student_affairs",
        "business_no": "SA20260001",
        "request_id": "progress-1",
    }
    arguments.update(overrides)
    return asyncio.run(service.query(**arguments))


def test_mock_adapter_returns_actor_scoped_reviewing_and_completed_fixtures() -> None:
    reviewing = _query(_service())
    completed = _query(
        _service(), system_code="academic_affairs", business_no="AA20260002"
    )

    assert reviewing.status == "reviewing"
    assert reviewing.next_action == "等待审核结果"
    assert reviewing.business_no_masked == "******0001"
    assert completed.status == "completed"
    assert completed.next_action is None
    assert completed.business_no_masked == "******0002"
    assert reviewing.updated_at == NOW


def test_same_mock_adapter_hides_fixtures_from_other_actor() -> None:
    service = _service(MockCampusSystemAdapter(USER_ID, now=lambda: NOW))
    with pytest.raises(ServiceProgressNotFound):
        _query(service, actor_user_id=OTHER_USER_ID)


def test_unsupported_system_and_missing_fixture_are_stable_errors() -> None:
    with pytest.raises(CampusSystemUnsupported):
        _query(_service(), system_code="finance")
    with pytest.raises(ServiceProgressNotFound):
        _query(_service(), business_no="SA20269999")


def test_timeout_retries_once_after_50ms_then_returns_503() -> None:
    sleep = AsyncMock()
    adapter = MockCampusSystemAdapter(USER_ID, now=lambda: NOW)
    adapter.query_progress = AsyncMock(side_effect=adapter.query_progress)
    service = _service(adapter, sleep=sleep)

    with pytest.raises(CampusSystemTimedOut):
        _query(service, business_no="MOCK-TIMEOUT")

    assert adapter.query_progress.await_count == 2
    sleep.assert_awaited_once_with(0.05)


def test_invalid_response_and_disabled_mock_are_safe_503_errors() -> None:
    with pytest.raises(CampusSystemInvalidResponse):
        _query(_service(), business_no="MOCK-INVALID")
    with pytest.raises(CampusSystemUnavailable):
        _query(ServiceProgressService(adapter=None))


def test_log_and_audit_only_contain_hash_last4_and_bounded_metadata(caplog) -> None:
    session = MagicMock()
    session.begin = MagicMock(return_value=_Transaction())
    audit = MagicMock()
    business_no = "SA-SENSITIVE-1234"
    caplog.set_level(logging.INFO)
    with pytest.raises(ServiceProgressNotFound):
        _query(
            _service(
                MockCampusSystemAdapter(USER_ID, now=lambda: NOW),
                session=session,
                audit=audit,
                monotonic=MagicMock(side_effect=[1.0, 1.012]),
            ),
            business_no=business_no,
        )

    audit_data = audit.record_failure.call_args.kwargs["after_data"]
    assert audit_data["business_no_last4"] == "1234"
    assert len(audit_data["business_no_sha256"]) == 64
    assert audit_data["result"] == "SERVICE_PROGRESS_NOT_FOUND"
    rendered_audit = str(audit.record_failure.call_args.kwargs)
    rendered_logs = " ".join(record.getMessage() for record in caplog.records)
    assert business_no not in rendered_audit
    assert business_no not in rendered_logs
