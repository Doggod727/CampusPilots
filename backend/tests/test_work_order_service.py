import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.campus_service.models import WorkOrder, WorkOrderEvent
from app.modules.campus_service.work_order_errors import CampusNotFound, WorkOrderNotFound
from app.modules.campus_service.work_orders import (
    CreateWorkOrderCommand,
    WorkOrderService,
)
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import (
    IdempotencyDecision,
    IdempotencyReplay,
)

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, 16, 30, tzinfo=UTC)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=USER_ID,
        username="student01",
        display_name="张同学",
        email=None,
        department=None,
        status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "普通学生"),),
        permissions=("work_order:create",),
        last_login_at=None,
        created_at=NOW,
        version=1,
    )


def _command() -> CreateWorkOrderCommand:
    return CreateWorkOrderCommand(
        campus_code="main",
        dormitory_area="梅园",
        building="3号楼",
        room="301",
        fault_category="plumbing",
        description="洗手池下方持续漏水，需要尽快检修",
        preferred_start_at=datetime(2026, 7, 18, 1, tzinfo=UTC),
        preferred_end_at=datetime(2026, 7, 18, 3, tzinfo=UTC),
    )


def _service(*, campus=object(), decision=None):
    session = MagicMock()
    session.begin = MagicMock(return_value=_Transaction())
    session.flush = AsyncMock()
    campuses = MagicMock()
    campuses.get_enabled_campus = AsyncMock(return_value=campus)
    work_orders = MagicMock()
    work_orders.allocate_order_no = AsyncMock(return_value="WO-20260717-0001")
    events = MagicMock()
    idempotency = MagicMock()
    idempotency.begin = AsyncMock(
        return_value=decision or IdempotencyDecision(record_id=uuid4())
    )
    idempotency.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    service = WorkOrderService(
        session=session,
        campuses=campuses,
        work_orders=work_orders,
        events=events,
        idempotency=idempotency,
        audit=audit,
        now=lambda: NOW,
    )
    return service, session, campuses, work_orders, events, idempotency, audit


def _persisted_order() -> WorkOrder:
    return WorkOrder(
        id=UUID("70000000-0000-4000-8000-000000000001"),
        order_no="WO-20260716-0001",
        created_by=USER_ID,
        campus_code="main",
        dormitory_area="梅园",
        building="3号楼",
        room="301",
        fault_category="plumbing",
        description="洗手池下方持续漏水，需要尽快检修",
        preferred_start_at=NOW,
        preferred_end_at=datetime(2026, 7, 16, 18, tzinfo=UTC),
        status="submitted",
        assigned_to=None,
        assigned_department_id=None,
        rejection_reason=None,
        completion_note=None,
        version=1,
        submitted_at=NOW,
        accepted_at=None,
        processing_at=None,
        completed_at=None,
        cancelled_at=None,
        rejected_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_create_builds_order_initial_event_audit_and_idempotent_response() -> None:
    service, session, campuses, orders, events, idempotency, audit = _service()

    result = asyncio.run(
        service.create(
            actor=_actor(),
            command=_command(),
            idempotency_key="create-1",
            request_id="request-create-1",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    )

    assert result.status_code == 201
    assert result.body["data"]["status"] == "submitted"
    assert result.body["data"]["rating"] is None
    assert result.body["data"]["order_no"] == "WO-20260717-0001"
    campuses.get_enabled_campus.assert_awaited_once_with("main")
    orders.allocate_order_no.assert_awaited_once_with(date(2026, 7, 17))
    work_order = orders.add.call_args.args[0]
    event = events.append.call_args.args[0]
    assert work_order.created_by == USER_ID and work_order.version == 1
    assert event.sequence_no == 1 and event.from_status is None
    assert event.snapshot == {
        "work_order_id": str(work_order.id),
        "status": "submitted",
        "campus_code": "main",
        "fault_category": "plumbing",
        "version": 1,
    }
    assert "room" not in event.snapshot and "description" not in event.snapshot
    session.flush.assert_awaited_once()
    audit_payload = audit.record_success.call_args.kwargs
    assert audit_payload["after_data"] == {
        "status": "submitted",
        "campus_code": "main",
        "fault_category": "plumbing",
    }
    assert "description" not in str(audit_payload)
    complete = idempotency.complete.await_args.kwargs
    assert complete["response_status"] == 201
    assert complete["resource_id"] == str(work_order.id)


def test_create_replays_original_envelope_without_touching_business_data() -> None:
    replay_body = {
        "code": "OK",
        "message": "success",
        "data": {"id": str(uuid4())},
        "request_id": "original-request",
        "timestamp": NOW.isoformat(),
    }
    decision = IdempotencyDecision(
        record_id=uuid4(),
        replay=IdempotencyReplay(201, replay_body, "work_order", replay_body["data"]["id"]),
    )
    service, session, campuses, orders, events, idempotency, audit = _service(
        decision=decision
    )

    result = asyncio.run(
        service.create(
            actor=_actor(),
            command=_command(),
            idempotency_key="create-1",
            request_id="new-request",
        )
    )

    assert result.body == replay_body
    assert result.request_id == "original-request"
    campuses.get_enabled_campus.assert_not_awaited()
    orders.add.assert_not_called()
    events.append.assert_not_called()
    session.flush.assert_not_awaited()
    audit.record_success.assert_not_called()


def test_create_rejects_disabled_or_missing_campus_before_number_allocation() -> None:
    service, _, campuses, orders, events, _, audit = _service(campus=None)

    with pytest.raises(CampusNotFound) as exc_info:
        asyncio.run(
            service.create(
                actor=_actor(),
                command=_command(),
                idempotency_key="create-2",
                request_id="request-create-2",
            )
        )

    assert exc_info.value.code == "CAMPUS_NOT_FOUND"
    campuses.get_enabled_campus.assert_awaited_once()
    orders.allocate_order_no.assert_not_awaited()
    events.append.assert_not_called()
    audit.record_success.assert_not_called()


def test_list_visible_loads_scopes_and_builds_pagination() -> None:
    service, _, _, orders, _, _, _ = _service()
    scopes = MagicMock()
    scopes.get_for_user = AsyncMock(return_value=())
    service._scopes = scopes
    order = _persisted_order()
    orders.list_visible = AsyncMock(return_value=(((order, None),), 21))

    result = asyncio.run(
        service.list_visible(
            actor=_actor(),
            page=2,
            page_size=20,
            status="submitted",
            campus_code="main",
            assigned_to_me=False,
        )
    )

    assert result.pagination.total == 21
    assert result.pagination.total_pages == 2
    assert result.items[0].id == order.id
    scopes.get_for_user.assert_awaited_once_with(USER_ID)
    assert orders.list_visible.await_args.kwargs["scopes"] == ()


def test_detail_and_events_hide_missing_or_out_of_scope_order() -> None:
    service, _, _, orders, events, _, _ = _service()
    orders.get_visible = AsyncMock(return_value=None)

    with pytest.raises(WorkOrderNotFound):
        asyncio.run(service.get_visible(actor=_actor(), work_order_id=UUID(int=8)))
    with pytest.raises(WorkOrderNotFound):
        asyncio.run(service.list_events(actor=_actor(), work_order_id=UUID(int=8)))
    events.list_timeline.assert_not_called()


def test_event_query_returns_stable_timeline_after_visibility_check() -> None:
    service, _, _, orders, events, _, _ = _service()
    order = _persisted_order()
    orders.get_visible = AsyncMock(return_value=(order, None))
    event = WorkOrderEvent(
        id=UUID(int=10),
        work_order_id=order.id,
        sequence_no=1,
        event_type="submitted",
        from_status=None,
        to_status="submitted",
        actor_user_id=USER_ID,
        actor_role="student",
        reason=None,
        snapshot={},
        created_at=NOW,
    )
    events.list_timeline = AsyncMock(return_value=(event,))

    result = asyncio.run(service.list_events(actor=_actor(), work_order_id=order.id))

    assert [item.sequence_no for item in result.items] == [1]
