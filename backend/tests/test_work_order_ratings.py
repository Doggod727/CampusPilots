import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.campus_service.models import WorkOrder
from app.modules.campus_service.work_order_errors import (
    WorkOrderAlreadyRated,
    WorkOrderNotCompleted,
    WorkOrderNotFound,
)
from app.modules.campus_service.work_orders import RateWorkOrderCommand, WorkOrderService
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import (
    IdempotencyConflict,
    IdempotencyDecision,
    IdempotencyReplay,
)

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
ORDER_ID = UUID("70000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, 10, tzinfo=UTC)


class _Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False


def _actor(user_id=USER_ID):
    return AuthenticatedUser(
        user_id, "student01", "学生", None, None, "active",
        (AuthenticatedRole(uuid4(), "student", "学生"),), (), None, NOW, 1,
    )


def _order(status="completed"):
    order = MagicMock(spec=WorkOrder)
    order.id = ORDER_ID; order.created_by = USER_ID; order.status = status
    return order


def _service(order, *, existing_rating=None, decision=None):
    session = MagicMock(); session.begin = MagicMock(return_value=_Transaction()); session.flush = AsyncMock()
    orders = MagicMock(); orders.get_owner_for_update = AsyncMock(return_value=order)
    orders.get_rating = AsyncMock(return_value=existing_rating)
    idempotency = MagicMock(); idempotency.begin = AsyncMock(return_value=decision or IdempotencyDecision(uuid4()))
    idempotency.complete = AsyncMock(return_value=True); audit = MagicMock()
    service = WorkOrderService(
        session=session, campuses=MagicMock(), work_orders=orders, events=MagicMock(),
        idempotency=idempotency, audit=audit, now=lambda: NOW,
    )
    return service, session, orders, idempotency, audit


def _run(service, *, actor=None, comment="维修及时，处理结果很好"):
    return asyncio.run(service.rate(
        actor=actor or _actor(), work_order_id=ORDER_ID,
        command=RateWorkOrderCommand(5, comment), idempotency_key="rating-1",
        request_id="rating-request-1",
    ))


def test_owner_rates_completed_order_once_with_redacted_audit() -> None:
    service, session, orders, idempotency, audit = _service(_order())
    result = _run(service)
    rating = orders.add_rating.call_args.args[0]
    assert result.status_code == 201
    assert result.body["data"]["score"] == 5
    assert rating.work_order_id == ORDER_ID and rating.user_id == USER_ID
    assert rating.comment == "维修及时，处理结果很好"
    assert audit.record_success.call_args.kwargs["after_data"] == {"score": 5, "has_comment": True}
    assert rating.comment not in str(audit.record_success.call_args.kwargs)
    session.flush.assert_awaited_once()
    assert idempotency.complete.await_args.kwargs["response_status"] == 201


def test_non_owner_is_hidden_before_status_and_duplicate_checks() -> None:
    service, _, orders, _, _ = _service(None)
    with pytest.raises(WorkOrderNotFound):
        _run(service, actor=_actor(UUID(int=99)))
    orders.get_rating.assert_not_awaited()


def test_non_completed_and_duplicate_ratings_are_stable_conflicts() -> None:
    with pytest.raises(WorkOrderNotCompleted):
        _run(_service(_order("processing"))[0])
    with pytest.raises(WorkOrderAlreadyRated):
        _run(_service(_order(), existing_rating=object())[0])


def test_rating_replays_original_201_without_business_queries() -> None:
    body = {"code": "OK", "message": "success", "data": {"score": 5}, "request_id": "original", "timestamp": NOW.isoformat()}
    decision = IdempotencyDecision(uuid4(), IdempotencyReplay(201, body, "work_order_rating", str(UUID(int=8))))
    service, session, orders, _, audit = _service(_order(), decision=decision)
    result = _run(service)
    assert result.body == body and result.request_id == "original"
    orders.get_owner_for_update.assert_not_awaited(); orders.add_rating.assert_not_called()
    session.flush.assert_not_awaited(); audit.record_success.assert_not_called()


def test_rating_rejects_pending_idempotency_record_before_owner_lookup() -> None:
    decision = IdempotencyDecision(uuid4(), pending=True)
    service, _, orders, _, _ = _service(_order(), decision=decision)
    with pytest.raises(IdempotencyConflict):
        _run(service)
    orders.get_owner_for_update.assert_not_awaited()
