import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.campus_service.models import WorkOrder
from app.modules.campus_service.work_order_access import WorkOrderScope
from app.modules.campus_service.work_order_errors import (
    ResourceVersionConflict,
    WorkOrderIllegalTransition,
    WorkOrderNotFound,
)
from app.modules.campus_service.work_orders import (
    TransitionWorkOrderCommand,
    WorkOrderService,
)
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser, PermissionDenied
from app.modules.platform.idempotency import IdempotencyDecision, IdempotencyReplay

OWNER_ID = UUID("90000000-0000-4000-8000-000000000001")
STAFF_ID = UUID("90000000-0000-4000-8000-000000000003")
ORDER_ID = UUID("70000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, 9, tzinfo=UTC)


class _Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False


def _actor(user_id, role, permissions=()):
    return AuthenticatedUser(
        user_id, role + "01", role, None, None, "active",
        (AuthenticatedRole(uuid4(), role, role),), tuple(permissions), None, NOW, 1,
    )


def _order(status="submitted", version=1):
    return WorkOrder(
        id=ORDER_ID, order_no="WO-20260716-0001", created_by=OWNER_ID,
        campus_code="main", dormitory_area="梅园", building="3号楼", room="301",
        fault_category="plumbing", description="洗手池下方持续漏水，需要尽快检修",
        preferred_start_at=NOW, preferred_end_at=datetime(2026, 7, 16, 18, tzinfo=UTC),
        status=status, assigned_to=None, assigned_department_id=None,
        rejection_reason=None, completion_note=None, version=version,
        submitted_at=NOW, accepted_at=None, processing_at=None, completed_at=None,
        cancelled_at=None, rejected_at=None, created_at=NOW, updated_at=NOW,
    )


def _service(order, *, actor_scopes=(), decision=None):
    session = MagicMock(); session.begin = MagicMock(return_value=_Transaction()); session.flush = AsyncMock()
    campuses = MagicMock(); orders = MagicMock(); events = MagicMock(); scopes = MagicMock()
    orders.get_visible_for_update = AsyncMock(return_value=order)
    events.next_sequence = AsyncMock(return_value=2)
    scopes.get_for_user = AsyncMock(return_value=actor_scopes)
    idempotency = MagicMock()
    idempotency.begin = AsyncMock(return_value=decision or IdempotencyDecision(record_id=uuid4()))
    idempotency.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    service = WorkOrderService(
        session=session, campuses=campuses, work_orders=orders, events=events,
        idempotency=idempotency, audit=audit, scopes=scopes, now=lambda: NOW,
    )
    return service, session, orders, events, idempotency, audit


def _command(target="accepted", version=1, completion_note=None):
    return TransitionWorkOrderCommand(target, "状态流转原因", completion_note, version)


def _run(service, actor, command=None):
    return asyncio.run(service.transition(
        actor=actor, work_order_id=ORDER_ID, command=command or _command(),
        idempotency_key="transition-1", request_id="transition-request-1",
    ))


def test_staff_accepts_scoped_order_with_lock_event_audit_and_idempotency() -> None:
    order = _order()
    scope = WorkOrderScope("main", ("梅园",))
    service, session, orders, events, idempotency, audit = _service(order, actor_scopes=(scope,))
    result = _run(service, _actor(STAFF_ID, "service_staff", ("work_order:transition",)))

    assert result.status_code == 200
    assert order.status == "accepted" and order.assigned_to == STAFF_ID and order.version == 2
    orders.get_visible_for_update.assert_awaited_once()
    event = events.append.call_args.args[0]
    assert event.sequence_no == 2 and event.from_status == "submitted" and event.to_status == "accepted"
    assert set(event.snapshot) == {"work_order_id", "status", "version", "assigned_to"}
    assert "梅园" not in str(event.snapshot) and "漏水" not in str(event.snapshot)
    assert "状态流转原因" not in str(audit.record_success.call_args.kwargs)
    session.flush.assert_awaited_once()
    assert idempotency.complete.await_args.kwargs["response_status"] == 200


def test_owner_can_cancel_submitted_without_staff_permission_or_scope() -> None:
    order = _order()
    service, _, _, events, _, _ = _service(order)
    _run(service, _actor(OWNER_ID, "student"), _command("cancelled"))
    assert order.status == "cancelled" and order.cancelled_at == NOW
    assert events.append.call_args.args[0].actor_role == "student"


def test_staff_requires_permission_and_matching_persisted_scope() -> None:
    scope = WorkOrderScope("main", ("梅园",))
    service, *_ = _service(_order(), actor_scopes=(scope,))
    with pytest.raises(PermissionDenied):
        _run(service, _actor(STAFF_ID, "service_staff"))
    service, *_ = _service(_order(), actor_scopes=())
    with pytest.raises(WorkOrderNotFound):
        _run(service, _actor(STAFF_ID, "service_staff", ("work_order:transition",)))


def test_visibility_precedes_version_and_state_validation() -> None:
    service, _, orders, *_ = _service(None)
    with pytest.raises(WorkOrderNotFound):
        _run(service, _actor(STAFF_ID, "service_staff", ("work_order:transition",)), _command(version=99))
    orders.get_visible_for_update.assert_awaited_once()


def test_version_conflict_and_completion_note_are_stable_409_errors() -> None:
    scope = (WorkOrderScope("main", ("梅园",)),)
    actor = _actor(STAFF_ID, "service_staff", ("work_order:transition",))
    with pytest.raises(ResourceVersionConflict):
        _run(_service(_order(version=2), actor_scopes=scope)[0], actor, _command(version=1))
    with pytest.raises(WorkOrderIllegalTransition):
        _run(_service(_order(status="processing"), actor_scopes=scope)[0], actor, _command("completed"))


def test_transition_replays_original_response_without_lock_or_event() -> None:
    body = {"code": "OK", "message": "success", "data": {}, "request_id": "original", "timestamp": NOW.isoformat()}
    decision = IdempotencyDecision(uuid4(), IdempotencyReplay(200, body, "work_order", str(ORDER_ID)))
    service, session, orders, events, _, audit = _service(_order(), decision=decision)
    result = _run(service, _actor(OWNER_ID, "student"), _command("cancelled"))
    assert result.body == body and result.request_id == "original"
    orders.get_visible_for_update.assert_not_awaited(); events.append.assert_not_called()
    session.flush.assert_not_awaited(); audit.record_success.assert_not_called()
